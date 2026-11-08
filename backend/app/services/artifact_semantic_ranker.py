"""可选的历史预览语义精排；默认只做本地检索，不外发预览。"""

import asyncio
import math
from types import SimpleNamespace

from sqlalchemy import select
from app.core.database import SessionLocal
from app.models.user_setting import UserSetting
from app.repositories.setting_repo import UserSettingRepository
from app.services.knowledge_index_service import KnowledgeEmbeddingService
from app.services.setting_service import SettingService
from app.services.tools.schemas import redact_sensitive_text


class FrozenEmbeddingSettings:
    def __init__(self, base_url, api_key):
        self.base_url, self.api_key = base_url, api_key

    def get_or_create_user_settings(self, user_id):
        return SimpleNamespace(knowledge_embedding_base_url=self.base_url)

    def resolve_knowledge_model_api_key(self, user_id, kind):
        return self.api_key


class ArtifactSemanticRanker:
    MAX_CANDIDATES = 24

    def __init__(self, *, sessions=SessionLocal, embedding_service=None, timeout_seconds=4):
        self.sessions, self.embedding_service, self.timeout = sessions, embedding_service, timeout_seconds

    def _settings(self, user_id):
        with self.sessions() as db:
            row = db.scalar(select(UserSetting).where(UserSetting.user_id == user_id))
            if not row or row.knowledge_embedding_provider == "ollama":
                return None
            service = SettingService(UserSettingRepository(db))
            return (row.knowledge_embedding_provider, row.knowledge_embedding_model,
                FrozenEmbeddingSettings(row.knowledge_embedding_base_url,
                    service.resolve_knowledge_model_api_key(user_id, "embedding")))

    async def rank(self, *, user_id, query, previews):
        async def compute():
            configured = await asyncio.to_thread(self._settings, user_id)
            if not configured:
                return {}, "online_embedding_not_configured"
            provider, model, settings = configured
            adapter = self.embedding_service or KnowledgeEmbeddingService(settings)
            batch = previews[:self.MAX_CANDIDATES]
            vectors = await adapter.embed_texts(user_id=user_id,
                knowledge_base=SimpleNamespace(embedding_provider=provider, embedding_model=model),
                texts=[redact_sensitive_text(query)[:300]] + [redact_sensitive_text(text)[:1000] for _, text in batch])
            if (len(vectors) != len(batch) + 1 or not vectors[0]
                    or any(len(v) != len(vectors[0]) or any(type(x) not in (int, float) or not math.isfinite(x) for x in v)
                           or math.hypot(*v) == 0 for v in vectors)):
                raise ValueError("invalid_embeddings")
            base, norm = vectors[0], math.hypot(*vectors[0])
            return {item[0]: sum(a*b for a,b in zip(base, vector)) / (norm*math.hypot(*vector))
                    for item, vector in zip(batch, vectors[1:])}, "online_semantic_and_local_terms"
        try:
            return await asyncio.wait_for(compute(), self.timeout)
        except Exception:
            return {}, "semantic_unavailable_local_fallback"
