"""后台有限补建记忆向量；网络等待不持有事务，失败不阻塞普通 Chat。"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import or_, select, text, update

from app.core.database import SessionLocal
from app.models.user_memory import UserMemory
from app.models.user_setting import UserSetting
from app.models.conversation import Conversation
from app.repositories.memory_repo import UserMemoryRepository
from app.repositories.setting_repo import UserSettingRepository
from app.services.setting_service import SettingService
from app.services.memory_policy import contains_credential
from app.services.memory_extraction_policy import redact_source
from app.services.memory_retrieval_service import MemoryRetrievalService, _FrozenEmbeddingSettings, text_hash, valid_vector
from app.services.knowledge_index_service import KnowledgeEmbeddingService


class MemoryIndexWorker:
    BATCH_SIZE = 32

    def __init__(self, *, session_factory=SessionLocal, embedding_service=None):
        self.sessions = session_factory
        self.embedding_service = embedding_service

    def _claim(self):
        now = datetime.now(timezone.utc)
        lease = now + timedelta(seconds=90)
        with self.sessions() as db:
            if db.get_bind().dialect.name != "postgresql":
                return None
            db.execute(text("SET LOCAL lock_timeout = '250ms'"))
            eligible = (UserMemory.status == "active", UserMemory.is_enabled.is_(True),
                UserMemory.memory_type.not_in(["profile", "instruction"]),
                UserMemory.sensitivity != "sensitive", UserMemory.search_embedding.is_(None),
                or_(UserMemory.expires_at.is_(None), UserMemory.expires_at > now),
                or_(UserMemory.embedding_retry_after.is_(None), UserMemory.embedding_retry_after <= now),
                UserSetting.memory_enabled.is_(True))
            uid = db.scalar(select(UserMemory.user_id).join(UserSetting, UserSetting.user_id == UserMemory.user_id)
                .where(*eligible).order_by(UserMemory.updated_at, UserMemory.id).limit(1))
            if not uid:
                return None
            rows = list(db.scalars(select(UserMemory).join(UserSetting, UserSetting.user_id == UserMemory.user_id)
                .where(*eligible, UserMemory.user_id == uid).order_by(UserMemory.updated_at, UserMemory.id)
                .with_for_update(of=UserMemory, skip_locked=True).limit(self.BATCH_SIZE)))
            settings = SettingService(UserSettingRepository(db))
            setting = settings.get_or_create_user_settings(uid)
            config = dict(provider=setting.knowledge_embedding_provider, model=setting.knowledge_embedding_model,
                dimensions=setting.knowledge_embedding_dimensions, base_url=setting.knowledge_embedding_base_url,
                api_key=settings.resolve_knowledge_model_api_key(uid, "embedding"))
            accepted = []
            repo = UserMemoryRepository(db)
            for row in rows:
                scope = row.project_id
                if scope is None and row.source_conversation_id:
                    source = db.scalar(select(Conversation).where(Conversation.id == row.source_conversation_id,
                        Conversation.user_id == uid))
                    scope = source.project_id if source else None
                permitted = db.scalar(select(UserMemory.id).where(UserMemory.id == row.id,
                    *repo._retrieval_filter(uid, scope))) is not None
                if not permitted or contains_credential(row.title, row.content):
                    db.execute(update(UserMemory).where(UserMemory.id == row.id).values(
                        embedding_retry_after=now + timedelta(days=1), updated_at=UserMemory.updated_at))
                    continue
                db.execute(update(UserMemory).where(UserMemory.id == row.id).values(
                    embedding_retry_after=lease, updated_at=UserMemory.updated_at))
                accepted.append(dict(id=row.id, version=row.version, title=row.title, content=row.content))
            db.commit()
            return uid, config, lease, accepted

    def _save(self, uid, config, lease, rows, vectors):
        with self.sessions() as db:
            # 短事务锁配置行，防止刚核对完模型又被切换、迟到向量覆盖失效标记。
            setting = db.scalar(select(UserSetting).where(UserSetting.user_id == uid).with_for_update())
            if not setting or not setting.memory_enabled:
                return
            current = dict(provider=setting.knowledge_embedding_provider, model=setting.knowledge_embedding_model,
                dimensions=setting.knowledge_embedding_dimensions, base_url=setting.knowledge_embedding_base_url)
            if MemoryRetrievalService.signature(current) != MemoryRetrievalService.signature(config):
                return
            for row, vector in zip(rows, vectors):
                db.execute(update(UserMemory).where(UserMemory.id == row["id"], UserMemory.user_id == uid,
                    UserMemory.version == row["version"], UserMemory.title == row["title"],
                    UserMemory.content == row["content"], UserMemory.status == "active",
                    UserMemory.is_enabled.is_(True), UserMemory.embedding_retry_after == lease).values(
                        embedding_vector=json.dumps(vector), embedding_signature=MemoryRetrievalService.signature(config),
                        embedding_text_hash=text_hash(SimpleNamespace(**row)), embedding_retry_after=None,
                        updated_at=UserMemory.updated_at))
            db.commit()

    async def run_once(self):
        try:
            snapshot = await asyncio.to_thread(self._claim)
            if not snapshot:
                return False
            uid, config, lease, rows = snapshot
            if not rows:
                return False
            adapter = self.embedding_service or KnowledgeEmbeddingService(_FrozenEmbeddingSettings(config))
            vectors = await asyncio.wait_for(adapter.embed_texts(user_id=uid,
                knowledge_base=SimpleNamespace(embedding_provider=config["provider"], embedding_model=config["model"]),
                texts=[redact_source(f'{row["title"]}\n{row["content"]}')[:4000] for row in rows]), 8)
            if len(vectors) != len(rows) or not all(valid_vector(v, config["dimensions"]) for v in vectors):
                return False
            await asyncio.to_thread(self._save, uid, config, lease, rows, vectors)
            return True
        except Exception:
            # 保留 90 秒有限重试间隔；不记录含用户原文/Provider 凭证的异常。
            return False
