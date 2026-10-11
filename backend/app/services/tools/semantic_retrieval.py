"""工具元数据的异步语义召回；短数据库快照与在线调用分开。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import threading
import time
from collections import OrderedDict
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


@dataclass(frozen=True)
class ToolEmbeddingConfig:
    user_id: str
    provider: str
    model: str
    dimensions: int
    base_url: str
    api_key: str | None = field(default=None, repr=False)

    def scope(self) -> str:
        # 凭据只参与哈希，轮换后不能沿用旧配置；哈希也不写入 Trace。
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
    """工具向量按账号/模型/正文隔离缓存；不缓存用户问题或结果正文。"""

    MAX_TOOLS = 128
    BATCH_SIZE = 32
    CACHE_SIZE = 512
    CACHE_TTL_SECONDS = 3600
    _cache: OrderedDict[str, tuple[float, tuple[float, ...]]] = OrderedDict()
    _cache_lock = threading.Lock()

    def __init__(self, *, user_id: str | None = None, config: ToolEmbeddingConfig | None = None,
                 embedding_service=None, session_factory=SessionLocal, timeout_seconds: float = 6):
        self.user_id = user_id
        self.config = config
        self.embedding_service = embedding_service
        self.sessions = session_factory
        self.timeout_seconds = timeout_seconds

    def _snapshot(self) -> ToolEmbeddingConfig | None:
        if not self.user_id:
            return None
        with self.sessions() as db:
            row = db.scalar(select(UserSetting).where(UserSetting.user_id == self.user_id))
            if row is None or row.knowledge_embedding_provider == 'ollama':
                return None
            setting_service = SettingService(UserSettingRepository(db))
            return ToolEmbeddingConfig(self.user_id, row.knowledge_embedding_provider,
                row.knowledge_embedding_model, row.knowledge_embedding_dimensions,
                row.knowledge_embedding_base_url,
                setting_service.resolve_knowledge_model_api_key(self.user_id, 'embedding'))

    @staticmethod
    def tool_text(tool: ToolDefinition) -> str:
        # 不含 adapter、负向适用场景及返回内容；新工具仅需已有元数据，无新增类别规则。
        inputs = ' '.join(f'{name}: {value.get("description", "")}'
            for name, value in tool.input_schema.get('properties', {}).items() if isinstance(value, dict))
        return redact_source('\n'.join([tool.display_name, tool.description,
            ' '.join(tool.when_to_use), inputs]))[:1800]

    @staticmethod
    def _valid(vector, dimensions: int) -> bool:
        return (isinstance(vector, list) and len(vector) == dimensions
            and all(isinstance(value, (float, int)) and not isinstance(value, bool)
                    and math.isfinite(value) for value in vector)
            and 0 < math.hypot(*vector) < math.inf)

    @classmethod
    def _get(cls, key: str):
        with cls._cache_lock:
            item = cls._cache.get(key)
            if item and time.monotonic() - item[0] < cls.CACHE_TTL_SECONDS:
                cls._cache.move_to_end(key)
                return item[1]
            cls._cache.pop(key, None)
        return None

    @classmethod
    def _put(cls, key: str, vector):
        with cls._cache_lock:
            cls._cache[key] = (time.monotonic(), tuple(vector))
            cls._cache.move_to_end(key)
            while len(cls._cache) > cls.CACHE_SIZE:
                cls._cache.popitem(last=False)

    async def scores(self, *, query: str, definitions: list[ToolDefinition]) -> tuple[dict[str, float], dict]:
        start = time.perf_counter()
        diagnostics = dict(mode='rules', fallback_reason=None, cache_hits=0, embedded_tools=0)
        async def retrieve():
            config = self.config or await asyncio.to_thread(self._snapshot)
            if not config or config.provider == 'ollama':
                return {}, {**diagnostics, 'fallback_reason': 'online_embedding_not_configured',
                            'elapsed_ms': round((time.perf_counter()-start)*1000, 1)}
            if len(definitions) > self.MAX_TOOLS:
                return {}, {**diagnostics, 'fallback_reason': 'catalog_exceeds_semantic_limit', 'elapsed_ms': 0}
            adapter = self.embedding_service or KnowledgeEmbeddingService(_FrozenSettings(config))
            texts = [self.tool_text(tool) for tool in definitions]
            scope = config.scope()
            keys = [hashlib.sha256(f'{scope}:{tool.tool_key}:{text}'.encode()).hexdigest()
                    for tool, text in zip(definitions, texts)]
            vectors = [self._get(key) for key in keys]
            missing = [index for index, vector in enumerate(vectors) if vector is None]
            diagnostics['cache_hits'] = len(vectors)-len(missing)
            kb = SimpleNamespace(embedding_provider=config.provider, embedding_model=config.model)
            # 首批合并 query，暖缓存只调用一次 query embedding；原问题不写缓存。
            query_vector = None
            for offset in range(0, max(1, len(missing)), self.BATCH_SIZE):
                batch = missing[offset:offset+self.BATCH_SIZE]
                input_texts = ([redact_source(query)[:2000]] if query_vector is None else []) + [texts[i] for i in batch]
                response = await adapter.embed_texts(user_id=config.user_id, knowledge_base=kb, texts=input_texts)
                if len(response) != len(input_texts) or not all(self._valid(v, config.dimensions) for v in response):
                    raise ValueError('invalid_embeddings')
                if query_vector is None:
                    query_vector, response = response[0], response[1:]
                for index, vector in zip(batch, response):
                    vectors[index] = tuple(vector)
                    self._put(keys[index], vector)
                diagnostics['embedded_tools'] += len(batch)
            qnorm = math.hypot(*query_vector)
            scores = {}
            for tool, vector in zip(definitions, vectors):
                norm = math.hypot(*vector)
                scores[tool.tool_key] = math.fsum((a/qnorm)*(b/norm) for a, b in zip(query_vector, vector))
            diagnostics['mode'] = 'hybrid'
            return scores, {**diagnostics, 'elapsed_ms': round((time.perf_counter()-start)*1000, 1),
                            'model': config.model}
        try:
            return await asyncio.wait_for(retrieve(), timeout=self.timeout_seconds)
        except Exception as exc:
            # CancelledError 不在 Exception 内，取消必须向上游传播；错误正文可能含密钥，禁止输出。
            diagnostics['fallback_reason'] = 'embedding_timeout' if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) else 'embedding_unavailable'
            return {}, {**diagnostics, 'elapsed_ms': round((time.perf_counter()-start)*1000, 1)}
