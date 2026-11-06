"""记忆专用混合召回：短数据库快照、异步向量调用、版本复核和实际注入诊断。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
import numpy as np

from sqlalchemy import select, update
from sqlalchemy.exc import SQLAlchemyError

from app.core.database import SessionLocal
from app.models.user_memory import UserMemory
from app.models.conversation import Conversation
from app.models.user_setting import UserSetting
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.memory_repo import UserMemoryRepository
from app.repositories.setting_repo import UserSettingRepository
from app.services.knowledge_index_service import KnowledgeEmbeddingService
from app.services.memory_extraction_policy import redact_source
from app.services.memory_service import MemoryService
from app.services.memory_policy import contains_credential
from app.services.setting_service import SettingService
from app.services.memory_read_cache import MemoryReadCache


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

    def __init__(self, *, session_factory=SessionLocal, embedding_service=None, timeout_seconds: float = 8,
                 read_cache=None):
        self.sessions = session_factory
        self.embedding_service = embedding_service
        self.timeout_seconds = timeout_seconds
        self.read_cache = read_cache if read_cache is not None else MemoryReadCache()

    def _snapshot(self, user_id: str, project_id: str | None, query="", max_chars=4000):
        with self.sessions() as db:
            settings = SettingService(UserSettingRepository(db))
            setting = settings.get_or_create_user_settings(user_id)
            if not setting.memory_enabled:
                return [], None, {}
            config = dict(provider=setting.knowledge_embedding_provider, model=setting.knowledge_embedding_model,
                          dimensions=setting.knowledge_embedding_dimensions, base_url=setting.knowledge_embedding_base_url,
                          api_key=settings.resolve_knowledge_model_api_key(user_id, "embedding"))
            repo = UserMemoryRepository(db)
            key = self.read_cache.key(db, user_id, project_id) if self.read_cache.enabled else None
            # 含凭证或被脱敏的问题不进缓存；问题只在 Redis 值字段内，绝不输出到日志。
            if len(query) > 2000 or redact_source(query) != query:
                key = None
            field = json.dumps([query, max_chars, setting.memory_max_chars, self.signature(config)], ensure_ascii=False)
            indexed = db.get_bind().dialect.name == "postgresql"
            metadata = repo.retrieval_metadata(user_id, project_id) if key or indexed else None
            # Redis 也是网络 I/O；短 PG 快照先结束，不在缓存等待中占住数据库连接。
            db.commit()
            cached, state = self.read_cache.get(key, field)
            if cached and cached.get("metadata") == metadata and (
                    not indexed or repo.retrieval_metadata(user_id, project_id) == metadata):
                expected = cached.get("hashes")
                scores = [cached.get("semantic"), cached.get("ranking")]
                if (isinstance(expected, dict) and all(isinstance(score, dict) and all(
                    isinstance(k, str) and isinstance(v, (int, float)) and not isinstance(v, bool)
                    and math.isfinite(v) and 0 <= v <= 1 for k, v in score.items()) for score in scores)):
                    rows = repo.list_for_retrieval(user_id, project_id, memory_ids=cached["ids"], include_vectors=False)
                    for row in rows:
                        db.expunge(row)
                    return rows, config, dict(hit=cached, state="hit", metadata=metadata,
                        indexed=indexed, eligible_count=None if indexed else len(metadata))
            candidates, has_facts, pending = (repo.retrieval_candidates(user_id, project_id, query, self.signature(config))
                                             if indexed else (None, False, set()))
            rows = repo.list_for_retrieval(user_id, project_id, memory_ids=candidates, include_vectors=not indexed)
            rows = [row for row in rows if row.sensitivity != "sensitive" and not contains_credential(row.title, row.content)]
            source_ids = {row.source_conversation_id for row in rows if row.source_conversation_id and not row.project_id}
            scopes = dict(db.execute(select(Conversation.id, Conversation.project_id).where(
                Conversation.id.in_(source_ids), Conversation.user_id == user_id)).all()) if source_ids else {}
            for row in rows:
                row._needs_embedding = row.id in pending
                row._retrieval_scope = row.project_id or (scopes.get(row.source_conversation_id, "__unresolved__")
                    if row.source_conversation_id else None)
                db.expunge(row)
            return rows, config, dict(key=key, field=field, metadata=metadata, indexed=indexed, has_facts=has_facts,
                eligible_count=None if indexed else len(rows),
                index_backlog_pending=bool(pending),
                index_backlog_may_remain=len(pending) >= self.INDEX_BATCH_SIZE,
                state="stale" if cached else state)

    def _database_semantic(self, user_id, project_id, vector, signature, dimensions):
        """网络阶段已结束，另开短事务取数据库向量候选并复制值。"""
        with self.sessions() as db:
            repo = UserMemoryRepository(db)
            scores, diagnostics = repo.semantic_candidates(user_id, project_id, vector, signature, dimensions)
            rows = repo.list_for_retrieval(user_id, project_id, memory_ids=list(scores), include_vectors=False)
            source_ids = {r.source_conversation_id for r in rows if r.source_conversation_id and not r.project_id}
            scopes = dict(db.execute(select(Conversation.id, Conversation.project_id).where(
                Conversation.id.in_(source_ids), Conversation.user_id == user_id)).all()) if source_ids else {}
            for row in rows:
                # 显式范围直接使用；旧来源范围仍由最终 MemoryService 复核。
                if row.project_id:
                    row._retrieval_scope = row.project_id
                elif row.source_conversation_id:
                    row._retrieval_scope = scopes.get(row.source_conversation_id, "__unresolved__")
                else:
                    row._retrieval_scope = None
                db.expunge(row)
            return rows, scores, diagnostics

    @staticmethod
    def signature(config: dict[str, Any]) -> str:
        # 不含凭证；同维度不同模型/供应商/地址也不能比较。
        return hashlib.sha256(json.dumps([config["provider"], config["base_url"].rstrip("/"),
            config["model"], config["dimensions"], "memory_embedding_v1"], ensure_ascii=False).encode()).hexdigest()

    @staticmethod
    def _cached_vectors(facts, signature, dimensions):
        cached, missing = {}, []
        for row in facts:
            vector = None
            try:
                raw = row.embedding_vector or ""
                # 先限制 JSON 字符集，拒绝布尔值/字符串/NaN；再由 JSON 解析和数组形状
                # 验证数字数组，避免逐维执行百万次 Python 类型判断。
                if re.fullmatch(r"[\[\]\dEe+.,\-\s]+", raw):
                    decoded = json.loads(raw)
                    array = np.asarray(decoded, dtype=np.float64)
                    if array.shape == (dimensions,) and np.isfinite(array).all():
                        scale = float(np.max(np.abs(array)))
                        norm = scale * float(np.linalg.norm(array / scale)) if scale else 0.
                        if math.isfinite(norm) and norm > 0:
                            vector = array
            except (ValueError, TypeError, OverflowError):
                pass
            if (row.embedding_signature == signature and row.embedding_text_hash == text_hash(row)
                    and vector is not None):
                cached[row.id] = vector
            else:
                missing.append(row)
        return cached, missing

    @classmethod
    def _rank(cls, rows, facts, cached, query_vector, query, user_id, project_id, database_scores=None):
        """词法/余弦及记忆规则都在线程中计算，避免阻塞 Web 事件循环。"""
        semantic = dict(database_scores or {})
        ids = [row.id for row in facts if row.id in cached]
        if query_vector and ids:
            matrix = np.array([cached[key] for key in ids], dtype=np.float64)
            matrix /= np.max(np.abs(matrix), axis=1)[:, None]
            matrix /= np.sqrt(np.einsum("ij,ij->i", matrix, matrix))[:, None]
            query_array = np.asarray(query_vector, dtype=np.float64)
            query_array /= np.max(np.abs(query_array))
            query_array /= np.linalg.norm(query_array)
            semantic.update(zip(ids, np.clip(np.einsum("ij,j->i", matrix, query_array), -1, 1).tolist()))
        terms = MemoryService._search_terms(query)
        lexical = {row.id: MemoryService._query_relevance_score(query, f"{row.title}\n{row.content}",
                   query_terms=terms) for row in facts}
        ranking = {}
        for scores, minimum in ((lexical, .000001), (semantic, cls.SEMANTIC_MIN)):
            ordered = sorted((key for key, value in scores.items() if value >= minimum), key=lambda key: (-scores[key], key))
            for rank, key in enumerate(ordered, 1):
                ranking[key] = ranking.get(key, 0) + .5 * 61 / (60 + rank)
        selected = MemoryService(None).select_memories_for_query(user_id, query=query, project_id=project_id,
            semantic_scores=semantic, ranking_scores=ranking, candidate_memories=rows, lexical_scores=lexical)
        return semantic, ranking, {row.id: text_hash(row) for row in rows}, [row.id for row in selected.memories]

    def _finalize(self, user_id, project_id, query, max_chars, signature, vectors, semantic, ranking, diagnostics,
                  cache_state=None, candidate_ids=None):
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
            if cache_state and cache_state.get("indexed") and service.repo.retrieval_metadata(user_id, project_id) != cache_state.get("metadata"):
                # 来源/事实在网络等待期间改变时，宁可本轮不注入，不能复用过期选择。
                return MemoryRetrievalResult(diagnostics={**diagnostics, "fallback_reason": "memory_changed_during_retrieval"})
            hit = (cache_state or {}).get("hit")
            current_rows = service.repo.list_for_retrieval(user_id, project_id,
                memory_ids=hit["ids"] if hit else candidate_ids, include_vectors=False)
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
            if (cache_state and not hit and cache_state.get("key") and not diagnostics.get("fallback_reason")
                    and not diagnostics.get("embedding_deferred")
                    and not cache_state.get("index_backlog_may_remain")):
                ids = [row.id for row in selection.memories]
                value = dict(
                    metadata=cache_state["metadata"], ids=ids,
                    hashes={row.id: text_hash(row) for row in selection.memories},
                    semantic={key: value for key, value in semantic.items() if key in ids},
                    ranking={key: value for key, value in ranking.items() if key in ids},
                    mode=diagnostics["mode"])
                db.commit()
                self.read_cache.put(cache_state["key"], cache_state["field"], value)
            return MemoryRetrievalResult(context, records, diagnostics)

    async def retrieve(self, *, user_id: str, project_id: str | None, query: str, max_chars: int) -> MemoryRetrievalResult:
        try:
            rows, config, cache_state = await asyncio.to_thread(self._snapshot, user_id, project_id, query, max_chars)
        except SQLAlchemyError:
            return MemoryRetrievalResult(diagnostics={"mode": "unavailable", "fallback_reason": "memory_database_unavailable"})
        diagnostics = dict(mode="lexical", eligible_count=cache_state.get("eligible_count", len(rows)),
                           read_cache=cache_state.get("state", "disabled"),
                           read_rows=len(rows), embedding_cache_hits=0,
                           embedding_generated=0, embedding_deferred=0, fallback_reason=None)
        diagnostics["database_candidates_bounded"] = bool(cache_state.get("indexed"))
        diagnostics["eligible_count_is_total"] = not bool(cache_state.get("indexed"))
        diagnostics["index_backlog_pending"] = cache_state.get("index_backlog_pending", False)
        diagnostics["embedding_deferred_is_total"] = not bool(cache_state.get("indexed"))
        semantic, ranking, generated = {}, {}, []
        cached, query_vector = {}, None
        signature = ""
        hit = cache_state.get("hit")
        if hit and config:
            diagnostics.update(mode=hit.get("mode", "lexical"), snapshot_hashes=hit["hashes"])
            return await asyncio.to_thread(self._finalize, user_id, project_id, query, max_chars,
                self.signature(config), [], hit["semantic"], hit["ranking"], diagnostics, cache_state)
        facts = [row for row in rows if row.memory_type not in {"profile", "instruction"}]
        clean_query = redact_source(query).strip()[:2000]
        if (facts or cache_state.get("has_facts")) and clean_query and config:
            signature = self.signature(config)
            dimensions = config["dimensions"]
            if cache_state.get("indexed"):
                # 原生索引已验证向量；生产路径不再把 JSON 向量传回 Python 解析。
                cached, missing = {}, [row for row in facts if row._needs_embedding]
            else:
                cached, missing = await asyncio.to_thread(self._cached_vectors, facts, signature, dimensions)
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
                query_vector = vectors[0]
                diagnostics.update(mode="hybrid", embedding_generated=len(generated))
            except Exception as exc:
                # 不将 URL、原始异常、响应正文或凭证送给模型/前端；召回仍可走词法。
                diagnostics["fallback_reason"] = "embedding_timeout" if isinstance(exc, (asyncio.TimeoutError, TimeoutError)) else "embedding_unavailable"
        database_scores = {}
        if query_vector and cache_state.get("indexed"):
            try:
                semantic_rows, database_scores, info = await asyncio.to_thread(self._database_semantic,
                    user_id, project_id, query_vector, signature, config["dimensions"])
                unique = {row.id: row for row in rows}
                unique.update({row.id: row for row in semantic_rows if row.id not in unique})
                rows = list(unique.values())
                facts = [row for row in rows if row.memory_type not in {"profile", "instruction"}]
                diagnostics.update(info, read_rows=len(rows))
            except SQLAlchemyError:
                diagnostics["fallback_reason"] = "semantic_database_unavailable"
        # 在已授权快照上先选出最多八条，再用 PG 重新校验；不重复搬运整批向量正文。
        semantic, ranking, hashes, candidate_ids = await asyncio.to_thread(
            self._rank, rows, facts, cached, query_vector, query, user_id, project_id, database_scores)
        diagnostics["snapshot_hashes"] = hashes
        return await asyncio.to_thread(self._finalize, user_id, project_id, query, max_chars,
            signature, generated, semantic, ranking, diagnostics, cache_state, candidate_ids)
