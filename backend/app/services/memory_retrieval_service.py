"""记忆专用混合召回：短数据库快照、异步向量调用、版本复核和实际注入诊断。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select, update

from app.core.database import SessionLocal
from app.models.user_memory import UserMemory
from app.models.user_setting import UserSetting
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.memory_repo import UserMemoryRepository
from app.repositories.setting_repo import UserSettingRepository
from app.services.knowledge_index_service import KnowledgeEmbeddingService
from app.services.memory_extraction_policy import redact_source
from app.services.memory_service import MemoryService
from app.services.memory_policy import contains_credential
from app.services.setting_service import SettingService


def text_hash(memory: UserMemory) -> str:
    """绑定正文、标题和版本，旧向量不得适用于新事实。"""
    return hashlib.sha256(f"{memory.version}:{memory.title}\n{memory.content}".encode()).hexdigest()


def valid_vector(vector: Any, dimensions: int) -> bool:
    if not isinstance(vector, list) or len(vector) != dimensions:
        return False
    try:
        if not all(isinstance(value, (int, float)) and not isinstance(value, bool)
                   and math.isfinite(value) for value in vector):
            return False
        norm = math.hypot(*vector)
        return math.isfinite(norm) and norm > 0
    except (OverflowError, TypeError, ValueError):
        return False


def cosine(left: list[float], right: list[float]) -> float:
    # 先归一化再点乘，避免异常数值平方后溢出或下溢，污染诊断 JSON。
    left_norm, right_norm = math.hypot(*left), math.hypot(*right)
    return max(-1.0, min(1.0, math.fsum(
        (a / left_norm) * (b / right_norm) for a, b in zip(left, right))))


@dataclass
class MemoryRetrievalResult:
    context_text: str | None = None
    records: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def count(self) -> int:
        return len(self.records)

    @property
    def chars(self) -> int:
        return len(self.context_text or "")

    def after_governance(self, memory_text: str) -> dict[str, Any]:
        """只核对治理层实际保留的结构化记忆段，避免历史/网页伪造标签干扰计数。"""
        lines = set(memory_text.splitlines())
        injected = [record for record in self.records if record["line"] in lines]
        public = [{key: value for key, value in record.items() if key != "line"} for record in injected]
        return {**self.diagnostics, "selected_count": len(self.records), "injected_count": len(public),
                "dropped_by_total_budget": len(self.records) - len(public), "injected": public,
                "injected_chars": len(memory_text) if injected else 0}


class _FrozenEmbeddingSettings:
    """复用现有 Provider 适配器，网络阶段只读取脱离 Session 的配置。"""

    def __init__(self, config: dict[str, Any]):
        self.config = config

    def get_or_create_user_settings(self, user_id: str):
        return SimpleNamespace(knowledge_embedding_base_url=self.config["base_url"])

    def resolve_knowledge_model_api_key(self, user_id: str, kind: str):
        return self.config["api_key"]


class MemoryRetrievalService:
    # 首次最多补 32 条，后续复用；所有合规事实仍参加词法召回。避免一次冷启动无界调用。
    INDEX_BATCH_SIZE = 32
    SEMANTIC_MIN = 0.45

    def __init__(self, *, session_factory=SessionLocal, embedding_service=None, timeout_seconds: float = 8):
        self.sessions = session_factory
        self.embedding_service = embedding_service
        self.timeout_seconds = timeout_seconds

    def _snapshot(self, user_id: str, project_id: str | None):
        with self.sessions() as db:
            settings = SettingService(UserSettingRepository(db))
            setting = settings.get_or_create_user_settings(user_id)
            if not setting.memory_enabled:
                return [], None
            config = dict(provider=setting.knowledge_embedding_provider, model=setting.knowledge_embedding_model,
                          dimensions=setting.knowledge_embedding_dimensions, base_url=setting.knowledge_embedding_base_url,
                          api_key=settings.resolve_knowledge_model_api_key(user_id, "embedding"))
            rows = UserMemoryRepository(db).list_for_retrieval(user_id, project_id)
            rows = [row for row in rows if row.sensitivity != "sensitive" and not contains_credential(row.title, row.content)]
            for row in rows:
                db.expunge(row)
            return rows, config

    @staticmethod
    def signature(config: dict[str, Any]) -> str:
        # 不含凭证；同维度不同模型/供应商/地址也不能比较。
        return hashlib.sha256(json.dumps([config["provider"], config["base_url"].rstrip("/"),
            config["model"], config["dimensions"], "memory_embedding_v1"], ensure_ascii=False).encode()).hexdigest()

    def _finalize(self, user_id, project_id, query, max_chars, signature, vectors, semantic, ranking, diagnostics):
        snapshot_hashes = diagnostics.pop("snapshot_hashes", {})
        with self.sessions() as db:
            setting = db.scalar(select(UserSetting).where(UserSetting.user_id == user_id))
            if setting is None or not setting.memory_enabled:
                return MemoryRetrievalResult(diagnostics={**diagnostics, "mode": "disabled"})
            current_signature = self.signature(dict(provider=setting.knowledge_embedding_provider,
                model=setting.knowledge_embedding_model, dimensions=setting.knowledge_embedding_dimensions,
                base_url=setting.knowledge_embedding_base_url))
            if signature and signature != current_signature:
                vectors, semantic, ranking = [], {}, {}
                diagnostics.update(mode="lexical", fallback_reason="embedding_settings_changed", embedding_generated=0)
            # 写缓存使用正文版本 CAS；撤销、更正或重新启用期间的旧网络结果不能复活记忆。
            for row, vector in vectors:
                db.execute(update(UserMemory).where(UserMemory.id == row.id, UserMemory.user_id == user_id,
                    UserMemory.version == row.version, UserMemory.status == "active", UserMemory.is_enabled.is_(True),
                    UserMemory.title == row.title, UserMemory.content == row.content).values(
                        embedding_vector=json.dumps(vector), embedding_signature=signature,
                        embedding_text_hash=text_hash(row), updated_at=UserMemory.updated_at)
                    .execution_options(synchronize_session=False))
            db.commit()
            service = MemoryService(UserMemoryRepository(db), ConversationRepository(db))
            current_rows = service.repo.list_for_retrieval(user_id, project_id)
            valid_ids = {row.id for row in current_rows if text_hash(row) == snapshot_hashes.get(row.id)}
            semantic = {key: value for key, value in semantic.items() if key in valid_ids}
            ranking = {key: value for key, value in ranking.items() if key in valid_ids}
            selection = service.select_memories_for_query(user_id, query=query, project_id=project_id,
                semantic_scores=semantic, ranking_scores=ranking, candidate_memories=current_rows)
            context, records = service.render_selection(selection.memories, max_chars=min(max_chars, setting.memory_max_chars))
            for record in records:
                row = next(item for item in selection.memories if item.id == record["id"])
                record["project_id"] = service._memory_scope(row, user_id)
                lexical = service._query_relevance_score(query, f"{row.title}\n{row.content}")
                record.update(reason="profile" if row.memory_type in {"profile", "instruction"} else
                    "hybrid" if lexical > 0 and semantic.get(row.id, 0) >= self.SEMANTIC_MIN else
                    "semantic" if semantic.get(row.id, 0) >= self.SEMANTIC_MIN else "lexical",
                    lexical_score=lexical, semantic_score=round(semantic.get(row.id, 0), 4))
            return MemoryRetrievalResult(context, records, diagnostics)

    async def retrieve(self, *, user_id: str, project_id: str | None, query: str, max_chars: int) -> MemoryRetrievalResult:
        rows, config = await asyncio.to_thread(self._snapshot, user_id, project_id)
        diagnostics = dict(mode="lexical", eligible_count=len(rows), embedding_cache_hits=0,
                           embedding_generated=0, embedding_deferred=0, fallback_reason=None)
        semantic, ranking, generated = {}, {}, []
        signature = ""
        facts = [row for row in rows if row.memory_type not in {"profile", "instruction"}]
        clean_query = redact_source(query).strip()[:2000]
        if facts and clean_query and config:
            signature = self.signature(config)
            dimensions = config["dimensions"]
            cached, missing = {}, []
            for row in facts:
                try:
                    vector = json.loads(row.embedding_vector or "null")
                except (ValueError, TypeError):
                    vector = None
                if (row.embedding_signature == signature and row.embedding_text_hash == text_hash(row)
                        and valid_vector(vector, dimensions)):
                    cached[row.id] = vector
                else:
                    missing.append(row)
            diagnostics["embedding_cache_hits"] = len(cached)
            batch = missing[:self.INDEX_BATCH_SIZE]
            diagnostics["embedding_deferred"] = max(0, len(missing) - len(batch))
            adapter = self.embedding_service or KnowledgeEmbeddingService(_FrozenEmbeddingSettings(config))
            try:
                vectors = await asyncio.wait_for(adapter.embed_texts(user_id=user_id,
                    knowledge_base=SimpleNamespace(embedding_provider=config["provider"], embedding_model=config["model"]),
                    texts=[clean_query, *[redact_source(f"{row.title}\n{row.content}")[:4000] for row in batch]]), self.timeout_seconds)
                if len(vectors) != len(batch) + 1 or not all(valid_vector(v, dimensions) for v in vectors):
                    raise ValueError("invalid_embedding")
                generated = list(zip(batch, vectors[1:]))
                cached.update({row.id: v for row, v in generated})
                semantic = {row.id: cosine(vectors[0], cached[row.id]) for row in facts if row.id in cached}
                diagnostics.update(mode="hybrid", embedding_generated=len(generated))
            except Exception as exc:
                # 不将 URL、原始异常、响应正文或凭证送给模型/前端；召回仍可走词法。
                diagnostics["fallback_reason"] = "embedding_timeout" if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) else "embedding_unavailable"
        lexical = {row.id: MemoryService._query_relevance_score(query, f"{row.title}\n{row.content}") for row in facts}
        for scores, minimum in ((lexical, 0.000001), (semantic, self.SEMANTIC_MIN)):
            ordered = sorted((key for key, value in scores.items() if value >= minimum), key=lambda key: (-scores[key], key))
            for rank, key in enumerate(ordered, 1):
                ranking[key] = ranking.get(key, 0) + 0.5 * 61 / (60 + rank)
        diagnostics["snapshot_hashes"] = {row.id: text_hash(row) for row in rows}
        return await asyncio.to_thread(self._finalize, user_id, project_id, query, max_chars,
            signature, generated, semantic, ranking, diagnostics)
