"""持久化工具粗召回；短数据库事务与在线Embedding分开。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import time
from dataclasses import dataclass, field
from types import SimpleNamespace

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.user_setting import UserSetting
from app.repositories.setting_repo import UserSettingRepository
from app.services.knowledge_index_service import KnowledgeEmbeddingService
from app.services.memory_extraction_policy import redact_source
from app.services.setting_service import SettingService
from app.services.tools.schemas import ToolDefinition
from app.services.tools.search_index import PostgresToolSearchIndex, ToolSearchDocument


@dataclass(frozen=True)
class ToolEmbeddingConfig:
    user_id: str
    provider: str
    model: str
    dimensions: int
    base_url: str
    api_key: str | None = field(default=None, repr=False)

    def scope(self) -> str:
        # 沿用旧缓存隔离语义：凭据只参与哈希，不保存明文或写入Trace。
        return hashlib.sha256(json.dumps([self.user_id, self.provider, self.model,
            self.dimensions, self.base_url.rstrip('/'), self.api_key], ensure_ascii=False).encode()).hexdigest()


class _FrozenSettings:
    def __init__(self, config: ToolEmbeddingConfig):
        self.config = config

    def get_or_create_user_settings(self, user_id):
        return SimpleNamespace(knowledge_embedding_base_url=self.config.base_url)

    def resolve_knowledge_model_api_key(self, user_id, kind):
        return self.config.api_key


class ToolSemanticRetriever:
    """粗召回最多30个，Chat有界补索引；大目录上线前用独立命令预建。"""

    MAX_TOOLS = 10000
    BATCH_SIZE = 32
    COARSE_LIMIT = 30

    def __init__(self, *, user_id: str | None = None, config: ToolEmbeddingConfig | None = None,
                 embedding_service=None, session_factory=SessionLocal, timeout_seconds: float = 6,
                 index_store=None):
        self.user_id = user_id
        self.config = config
        self.embedding_service = embedding_service
        self.sessions = session_factory
        self.timeout_seconds = timeout_seconds
        self.index = index_store if index_store is not None else PostgresToolSearchIndex(session_factory)

    def _snapshot(self) -> ToolEmbeddingConfig | None:
        if not self.user_id:
            return None
        with self.sessions() as db:
            row = db.scalar(select(UserSetting).where(UserSetting.user_id == self.user_id))
            if row is None or row.knowledge_embedding_provider == 'ollama':
                return None
            service = SettingService(UserSettingRepository(db))
            return ToolEmbeddingConfig(self.user_id, row.knowledge_embedding_provider,
                row.knowledge_embedding_model, row.knowledge_embedding_dimensions,
                row.knowledge_embedding_base_url, service.resolve_knowledge_model_api_key(self.user_id, 'embedding'))

    @staticmethod
    def tool_text(tool: ToolDefinition) -> str:
        # 不含负向适用场景、adapter、凭据或工具返回；类别变化也使旧说明失效。
        inputs = ' '.join(f'{name}: {value.get("description", "")}'
            for name, value in tool.input_schema.get('properties', {}).items() if isinstance(value, dict))
        return redact_source('\n'.join([tool.category, tool.display_name, tool.description,
            ' '.join(tool.when_to_use), inputs]))[:1800]

    @staticmethod
    def _valid(vector, dimensions: int) -> bool:
        return (isinstance(vector, list) and len(vector) == dimensions
            and all(isinstance(value, (float, int)) and not isinstance(value, bool)
                    and math.isfinite(value) for value in vector)
            and 0 < math.hypot(*vector) < math.inf)

    def _documents(self, definitions):
        return [ToolSearchDocument(tool.tool_key, tool.category, self.tool_text(tool)) for tool in definitions]

    async def _embed(self, config, texts):
        adapter = self.embedding_service or KnowledgeEmbeddingService(_FrozenSettings(config))
        vectors = await adapter.embed_texts(user_id=config.user_id,
            knowledge_base=SimpleNamespace(embedding_provider=config.provider, embedding_model=config.model), texts=texts)
        if len(vectors) != len(texts) or not all(self._valid(v, config.dimensions) for v in vectors):
            raise ValueError('invalid_embeddings')
        return vectors

    @staticmethod
    def _coarse(lexical, semantic, limit):
        ranks = {}
        for keys in (lexical, sorted(semantic, key=lambda k: (-semantic[k], k))):
            for rank, key in enumerate(keys, 1):
                ranks[key] = ranks.get(key, 0) + 1 / (60 + rank)
        return sorted(ranks, key=lambda k: (-ranks[k], k))[:limit]

    async def scores(self, *, query: str, definitions: list[ToolDefinition], categories=()) -> tuple[dict[str, float], dict]:
        start = time.perf_counter()
        diag = dict(mode='rules', fallback_reason=None, persistent_hits=0, embedded_tools=0,
                    eligible_count=len(definitions), coarse_limit=self.COARSE_LIMIT)

        async def retrieve():
            config = self.config or await asyncio.to_thread(self._snapshot)
            if not config or config.provider == 'ollama':
                diag['fallback_reason'] = 'online_embedding_not_configured'
                return {}
            if len(definitions) > self.MAX_TOOLS:
                diag['fallback_reason'] = 'catalog_exceeds_index_safety_limit'
                return {}
            documents = self._documents(definitions)
            ready = await asyncio.to_thread(self.index.prepare, config, documents)
            diag['persistent_hits'] = len(ready)
            lexical = await asyncio.to_thread(self.index.lexical, config, documents, query, categories, self.COARSE_LIMIT)
            diag['lexical_tool_keys'] = lexical
            by_key = {d.tool_key: d for d in documents}
            # 冷目录优先补当前关键词命中的条目；一次Chat不为千工具无限等待。
            pending = [by_key[key] for key in dict.fromkeys([*lexical, *sorted(by_key)]) if key not in ready]
            batch = pending[:self.BATCH_SIZE - 1]
            vectors = await self._embed(config, [redact_source(query)[:2000]] + [d.text for d in batch])
            if batch:
                saved = await asyncio.to_thread(self.index.save, config, batch, vectors[1:])
                ready.update(saved)
                diag['embedded_tools'] = len(saved)
            semantic, details = await asyncio.to_thread(self.index.semantic, config, documents, vectors[0], self.COARSE_LIMIT)
            diag.update(details, mode='hybrid', model=config.model, index_backend='postgres_pgvector',
                        pending_tools=len(documents) - len(ready), index_complete=len(ready) == len(documents))
            diag['coarse_tool_keys'] = self._coarse(lexical, {k: v for k, v in semantic.items() if v >= .35}, self.COARSE_LIMIT)
            return semantic

        try:
            semantic = await asyncio.wait_for(retrieve(), timeout=self.timeout_seconds)
        except Exception as exc:
            # CancelledError必须向上传播；不在Trace暴露远端错误、端点或密钥。
            diag['fallback_reason'] = ('embedding_timeout' if isinstance(exc, (asyncio.TimeoutError, TimeoutError))
                                       else 'index_or_embedding_unavailable')
            semantic = {}
        diag['elapsed_ms'] = round((time.perf_counter() - start) * 1000, 1)
        return semantic, diag

    async def warmup(self, definitions: list[ToolDefinition]) -> dict:
        """供管理命令预建全目录；不持有数据库事务等待Embedding。"""
        config = self.config or await asyncio.to_thread(self._snapshot)
        if not config or config.provider == 'ollama':
            raise RuntimeError('未配置在线Embedding，工具索引未创建。')
        if len(definitions) > self.MAX_TOOLS:
            raise RuntimeError('工具目录超过当前安全数量上限。')
        documents = self._documents(definitions)
        ready = await asyncio.to_thread(self.index.prepare, config, documents)
        pending = [d for d in documents if d.tool_key not in ready]
        saved_count = 0
        for offset in range(0, len(pending), self.BATCH_SIZE):
            batch = pending[offset:offset + self.BATCH_SIZE]
            vectors = await asyncio.wait_for(self._embed(config, [d.text for d in batch]), 30)
            saved_count += len(await asyncio.to_thread(self.index.save, config, batch, vectors))
        current = await asyncio.to_thread(self.index.prepare, config, documents)
        return dict(total=len(documents), ready=len(current), embedded_tools=saved_count,
                    index_complete=len(current) == len(documents), model=config.model)
