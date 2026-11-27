"""记忆专用混合召回：短数据库快照、异步向量调用、版本复核和实际注入诊断。"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from time import perf_counter
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
from app.services.memory_policy import contains_credential, lifetime
from app.services.setting_service import SettingService
from app.services.memory_read_cache import MemoryReadCache
from app.services.memory_injection_policy import (
    BEHAVIOR_HEADER, BEHAVIOR_REFERENCE_TITLE, BEHAVIOR_VALUES, MEMORY_REFERENCE_TITLE, MemoryReadDecision, PREFERENCE_TYPES, QUERY_LIMIT, RECALL_LIMIT, SEMANTIC_MIN,
    applicable_behavior_keys, behavior_preference, decide_memory_read, lexical_evidence, memory_query_terms,
)


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


def embedding_config(setting):
    return dict(provider=setting.knowledge_embedding_provider, model=setting.knowledge_embedding_model,
        dimensions=setting.knowledge_embedding_dimensions, base_url=setting.knowledge_embedding_base_url)


def lock_embedding_setting(db, user_id):
    return db.scalar(select(UserSetting).where(UserSetting.user_id == user_id)
        .execution_options(populate_existing=True).with_for_update())


def write_memory_vectors(db, user_id, signature, rows, *, enabled_only=True, lease=None):
    """调用者持有配置行锁并核对签名，提交前不释放配置锁。"""
    for row, vector in rows:
        conditions = [UserMemory.id == row.id, UserMemory.user_id == user_id, UserMemory.version == row.version,
                      UserMemory.status == "active", UserMemory.title == row.title, UserMemory.content == row.content]
        if enabled_only:
            conditions.append(UserMemory.is_enabled.is_(True))
        values = dict(embedding_vector=json.dumps(vector), embedding_signature=signature,
                      embedding_text_hash=text_hash(row), updated_at=UserMemory.updated_at)
        if lease is not None:
            conditions.append(UserMemory.embedding_retry_after == lease)
            values["embedding_retry_after"] = None
        db.execute(update(UserMemory).where(*conditions).values(**values).execution_options(synchronize_session=False))


@dataclass
class MemoryRetrievalResult:
    context_text: str | None = None
    records: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    behavior_context: str | None = None

    @property
    def count(self) -> int:
        return len(self.records)

    @property
    def chars(self) -> int:
        return len(self.context_text or "") + len(self.behavior_context or "")

    def recheck_for_prompt(self, db, *, user_id: str, project_id: str | None) -> None:
        """工具和摘要可能等待很久，构建 Prompt 前复核已选中的少量记忆。"""
        if not self.records:
            return
        started = perf_counter()
        enabled = db.scalar(select(UserSetting.memory_enabled).where(UserSetting.user_id == user_id))
        original_count = len(self.records)
        if enabled:
            service = MemoryService(UserMemoryRepository(db), ConversationRepository(db))
            versions = {record["id"]: record["version"] for record in self.records}
            rows = service.repo.list_for_retrieval(user_id, project_id, memory_ids=versions, include_vectors=False)
            current = {row.id for row in service.valid_sources(rows) if row.version == versions[row.id]
                       and service._memory_matches_project_scope(memory=row, user_id=user_id, project_id=project_id)
                       and row.sensitivity != "sensitive" and not contains_credential(row.title, row.content)}
            self.records = [record for record in self.records if record["id"] in current]
        else:
            self.records = []
        dropped = original_count - len(self.records)
        if dropped:
            for attribute, behavior in (("context_text", False), ("behavior_context", True)):
                header = (getattr(self, attribute) or "").split("\n", 1)[0]
                lines = [record["line"] for record in self.records if bool(record.get("behavior_key")) == behavior]
                setattr(self, attribute, "\n".join([header, *lines]) if lines else None)
        if self.diagnostics.get("read_cache") == "hit":
            self.diagnostics["read_cache_recheck_ms"] = round(self.diagnostics.get("read_cache_recheck_ms", 0) +
                (perf_counter() - started) * 1000, 2)
            if dropped:
                self.diagnostics["read_cache"] = "stale"
        self.diagnostics["dropped_at_prompt_recheck"] = dropped

    def after_governance(self, memory_text: str, behavior_text: str = "") -> dict[str, Any]:
        """只核对治理层实际保留的结构化记忆段，避免历史/网页伪造标签干扰计数。"""
        lines, behavior_lines = set(memory_text.splitlines()), set(behavior_text.splitlines())
        injected = [record for record in self.records if record["line"] in (
            behavior_lines if record.get("behavior_key") else lines)]
        public = [{key: value for key, value in record.items() if key != "line"} for record in injected]
        return {**self.diagnostics, "selected_count": len(self.records), "injected_count": len(public),
                "dropped_by_total_budget": len(self.records) - len(public), "injected": public,
                "injected_chars": len(memory_text) + len(behavior_text) if injected else 0}


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

    def __init__(self, *, session_factory=SessionLocal, embedding_service=None, timeout_seconds: float = 8,
                 read_cache=None):
        self.sessions = session_factory
        self.embedding_service = embedding_service
        self.timeout_seconds = timeout_seconds
        self.read_cache = read_cache if read_cache is not None else MemoryReadCache()

    def _snapshot(self, user_id: str, project_id: str | None, query="", max_chars=4000, turn_query=""):
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
            if len(query) > 2000 or redact_source(query) != query or redact_source(turn_query) != turn_query:
                key = None
            field = json.dumps(["on_demand_v2", query, turn_query, max_chars, setting.memory_max_chars,
                                self.signature(config)], ensure_ascii=False)
            indexed = db.get_bind().dialect.name == "postgresql"
            metadata = repo.retrieval_metadata(user_id, project_id) if key or indexed else None
            # Redis 也是网络 I/O；短 PG 快照先结束，不在缓存等待中占住数据库连接。
            db.commit()
            cached, state = self.read_cache.get(key, field)
            recheck_start = perf_counter() if cached else None
            if cached and cached.get("metadata") == metadata and (
                    not indexed or repo.retrieval_metadata(user_id, project_id) == metadata):
                expected = cached.get("hashes")
                scores = [cached.get("semantic"), cached.get("ranking")]
                if (isinstance(expected, dict) and all(isinstance(score, dict) and all(
                    isinstance(k, str) and isinstance(v, (int, float)) and not isinstance(v, bool)
                    and math.isfinite(v) and 0 <= v <= 1 for k, v in score.items()) for score in scores)):
                    rows = repo.list_for_retrieval(user_id, project_id, memory_ids=cached["ids"], include_vectors=False)
                    if not MemoryService(repo).source_issues(rows):
                        for row in rows:
                            db.expunge(row)
                        return rows, config, dict(hit=cached, state="hit", metadata=metadata,
                            cache_recheck_ms=(perf_counter() - recheck_start) * 1000,
                            indexed=indexed, eligible_count=None if indexed else len(metadata))
            candidates, has_facts, pending = (repo.retrieval_candidates(user_id, project_id, query, self.signature(config))
                                             if indexed else (None, False, set()))
            rows = repo.list_for_retrieval(user_id, project_id, memory_ids=candidates, include_vectors=not indexed)
            rows = MemoryService(repo).valid_sources([row for row in rows if row.sensitivity != "sensitive"
                                                     and not contains_credential(row.title, row.content)])
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
                state="stale" if cached else state, cache_recheck_ms=0.0)

    def _database_semantic(self, user_id, project_id, vector, signature, dimensions):
        """网络阶段已结束，另开短事务取数据库向量候选并复制值。"""
        with self.sessions() as db:
            repo = UserMemoryRepository(db)
            scores, diagnostics = repo.semantic_candidates(user_id, project_id, vector, signature, dimensions)
            rows = MemoryService(repo).valid_sources(repo.list_for_retrieval(
                user_id, project_id, memory_ids=list(scores), include_vectors=False))
            scores = {row.id: scores[row.id] for row in rows}
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
    def _rank(cls, rows, facts, cached, query_vector, query, user_id, project_id, database_scores=None, *, turn_query=None):
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
        terms = memory_query_terms(query, MemoryService._search_terms)
        lexical = {row.id: lexical_evidence(row, query, MemoryService._search_terms, terms) for row in rows}
        ranking = {}
        for scores, minimum in ((lexical, .000001), (semantic, SEMANTIC_MIN)):
            ordered = sorted((key for key, value in scores.items() if value >= minimum), key=lambda key: (-scores[key], key))
            for rank, key in enumerate(ordered, 1):
                ranking[key] = ranking.get(key, 0) + .5 * 61 / (60 + rank)
        candidates = [row for row in rows if not behavior_preference(row)]
        selector = MemoryService(None)
        selected = selector.select_memories_for_query(user_id, query=query, project_id=project_id,
            semantic_scores=semantic, ranking_scores=ranking, candidate_memories=candidates,
            lexical_scores=lexical, max_memories=RECALL_LIMIT, turn_query=turn_query)
        lexical_backup = selector.select_memories_for_query(user_id, query=query, project_id=project_id,
            candidate_memories=candidates, lexical_scores=lexical, max_memories=RECALL_LIMIT, turn_query=turn_query)
        candidate_ids = list(dict.fromkeys(row.id for row in [*selected.memories, *lexical_backup.memories]))
        return semantic, ranking, {row.id: text_hash(row) for row in rows}, candidate_ids

    def _finalize(self, user_id, project_id, query, max_chars, signature, vectors, semantic, ranking, diagnostics,
                  cache_state=None, candidate_ids=None, *, turn_query=None):
        snapshot_hashes = diagnostics.pop("snapshot_hashes", {})
        hit = (cache_state or {}).get("hit")
        finalize_started = perf_counter()
        with self.sessions() as db:
            setting = lock_embedding_setting(db, user_id) if vectors else db.scalar(
                select(UserSetting).where(UserSetting.user_id == user_id))
            if setting is None or not setting.memory_enabled:
                if hit:
                    diagnostics["read_cache"] = "stale"
                return MemoryRetrievalResult(diagnostics={**diagnostics, "mode": "disabled"})
            current_signature = self.signature(embedding_config(setting))
            if signature and signature != current_signature:
                vectors, semantic, ranking = [], {}, {}
                diagnostics.update(mode="lexical", fallback_reason="embedding_settings_changed", embedding_generated=0)
                if hit:
                    diagnostics["read_cache"] = "stale"
            # 写缓存使用正文版本 CAS；撤销、更正或重新启用期间的旧网络结果不能复活记忆。
            write_memory_vectors(db, user_id, signature, vectors)
            db.commit()
            service = MemoryService(UserMemoryRepository(db), ConversationRepository(db))
            if cache_state and cache_state.get("indexed") and service.repo.retrieval_metadata(user_id, project_id) != cache_state.get("metadata"):
                if hit:
                    diagnostics["read_cache"] = "stale"
                # 来源/事实在网络等待期间改变时，宁可本轮不注入，不能复用过期选择。
                return MemoryRetrievalResult(diagnostics={**diagnostics, "fallback_reason": "memory_changed_during_retrieval"})
            current_rows = service.valid_sources(service.repo.list_for_retrieval(user_id, project_id,
                memory_ids=hit["ids"] if hit else candidate_ids, include_vectors=False))
            valid_ids = {row.id for row in current_rows if text_hash(row) == snapshot_hashes.get(row.id)}
            if hit and valid_ids != set(hit["ids"]):
                diagnostics["read_cache"] = "stale"
            semantic = {key: value for key, value in semantic.items() if key in valid_ids}
            ranking = {key: value for key, value in ranking.items() if key in valid_ids}
            selection = service.select_memories_for_query(user_id, query=query, project_id=project_id,
                semantic_scores=semantic, ranking_scores=ranking, candidate_memories=[row for row in current_rows
                    if not behavior_preference(row)], turn_query=turn_query, max_memories=RECALL_LIMIT)
            limit = max(0, min(max_chars, setting.memory_max_chars, 20000))
            keys = applicable_behavior_keys(turn_query or query)
            behavior = self._select_behavior(service, user_id, project_id, keys) if limit and keys else []
            context, records, behavior_text, applied_behavior = self._render_for_chat(service, selection.memories, behavior, limit)
            for record in records:
                row = next(item for item in selection.memories if item.id == record["id"])
                record["project_id"] = service._memory_scope(row, user_id)
                lexical = lexical_evidence(row, query, service._search_terms)
                record.update(reason="hybrid" if lexical > 0 and semantic.get(row.id, 0) >= SEMANTIC_MIN else
                    "semantic" if semantic.get(row.id, 0) >= SEMANTIC_MIN else "lexical",
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
                write = self.read_cache.put(cache_state["key"], cache_state["field"], value)
                diagnostics["read_cache_write"] = write
                diagnostics["read_cache_capacity_clear"] = int(write == "capacity_clear")
            if hit:
                diagnostics["read_cache_recheck_ms"] = round(cache_state["cache_recheck_ms"] +
                    (perf_counter() - finalize_started) * 1000, 2)
            return MemoryRetrievalResult(context, [*records, *applied_behavior], diagnostics, behavior_text)

    @staticmethod
    def _select_behavior(service, user_id, project_id, keys):
        candidates = service.repo.behavior_candidates(user_id, project_id, keys)
        by_key = {}
        for row in service.valid_sources(candidates):
            preference = behavior_preference(row)
            scope = service._memory_scope(row, user_id)
            if not preference or preference[0] not in keys or scope not in {None, project_id}:
                continue
            if contains_credential(row.title, row.content):
                continue
            key, value = preference
            priority = (scope is not None, lifetime(row.title, row.content) == "temporary",
                        row.updated_at or row.created_at, row.id)
            previous = by_key.get(key)
            if previous is None or priority > previous[0]:
                by_key[key] = (priority, {"id": row.id, "version": row.version, "type": row.memory_type,
                    "project_id": scope, "behavior_key": key, "reason": key, "line": f"- {BEHAVIOR_VALUES[key, value]}"})
        return [by_key[key][1] for key in keys if key in by_key]

    @staticmethod
    def _render_for_chat(service, facts, behavior, limit):
        # 先预留一条能完整放下的相关事实，再选择偏好和剩余候选。
        factual = [row for row in facts if row.memory_type not in PREFERENCE_TYPES]
        fact_overhead = len(MEMORY_REFERENCE_TITLE) + len("【】\n")
        behavior_overhead = len(BEHAVIOR_REFERENCE_TITLE) + len("【】\n")
        reserved_text, reserved = service.render_selection(factual, max_chars=limit - fact_overhead, max_records=1)
        if reserved:
            factual_id = reserved[0]["id"]
            row = next(row for row in factual if row.id == factual_id)
            facts = [row, *[item for item in facts if item.id != row.id]]
        preference_text, applied = None, []
        for record in behavior:
            candidate = f"{preference_text or BEHAVIOR_HEADER}\n{record['line']}"
            reserved_chars = len(reserved_text) + fact_overhead + 2 if reserved_text else 0
            if len(candidate) + behavior_overhead + reserved_chars <= limit:
                preference_text = candidate
                applied.append(record)
        rest = limit - fact_overhead - (len(preference_text) + behavior_overhead + 2 if preference_text else 0)
        text, records = service.render_selection(facts, max_chars=rest, max_records=QUERY_LIMIT - len(applied))
        return text, records, preference_text, applied

    def _explicit_recall(self, user_id, project_id, query, max_chars, diagnostics):
        with self.sessions() as db:
            setting = db.scalar(select(UserSetting).where(UserSetting.user_id == user_id))
            if setting is None or not setting.memory_enabled:
                return MemoryRetrievalResult(diagnostics={**diagnostics, "mode": "disabled"})
            repo = UserMemoryRepository(db)
            rows = repo.list_preferences_for_recall(user_id, project_id, limit=RECALL_LIMIT * 3)
            service = MemoryService(repo, ConversationRepository(db))
            selection = service.select_memories_for_query(user_id, query=query, project_id=project_id,
                candidate_memories=rows, max_memories=RECALL_LIMIT, explicit_recall=True)
            context, records = service.render_selection(selection.memories,
                max_chars=min(max_chars, setting.memory_max_chars) - len(MEMORY_REFERENCE_TITLE) - len("【】\n"))
            selected = {row.id: row for row in selection.memories}
            for record in records:
                record.update(reason="explicit_recall", project_id=service._memory_scope(selected[record["id"]], user_id))
            return MemoryRetrievalResult(context, records, {**diagnostics, "mode": "explicit_recall", "read_rows": len(rows)})

    async def retrieve(self, *, user_id: str, project_id: str | None, query: str, max_chars: int,
                       decision: MemoryReadDecision | None = None) -> MemoryRetrievalResult:
        decision = decision or decide_memory_read(query)
        diagnostics = {"read_decision": decision.mode, "read_reason": decision.reason, "read_rows": 0,
                       "embedding_calls": 0}
        if max_chars <= 0 and decision.mode not in {"skipped", "disabled"}:
            return MemoryRetrievalResult(diagnostics={**diagnostics, "mode": "skipped", "read_reason": "memory_budget_empty"})
        if decision.mode in {"skipped", "disabled"}:
            return MemoryRetrievalResult(diagnostics={**diagnostics, "mode": decision.mode})
        turn_query, query = query, decision.query
        try:
            if decision.mode == "explicit_recall":
                return await asyncio.to_thread(self._explicit_recall, user_id, project_id, turn_query, max_chars, diagnostics)
            rows, config, cache_state = await asyncio.to_thread(self._snapshot, user_id, project_id, query, max_chars, turn_query)
        except SQLAlchemyError:
            return MemoryRetrievalResult(diagnostics={**diagnostics, "mode": "unavailable", "fallback_reason": "memory_database_unavailable"})
        diagnostics.update(mode="lexical", eligible_count=cache_state.get("eligible_count", len(rows)),
                           read_cache=cache_state.get("state", "disabled"),
                           read_rows=len(rows), embedding_cache_hits=0,
                           embedding_generated=0, embedding_deferred=0, embedding_ms=0.0,
                           read_cache_capacity_clear=0, read_cache_recheck_ms=0.0, fallback_reason=None)
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
                self.signature(config), [], hit["semantic"], hit["ranking"], diagnostics, cache_state, turn_query=turn_query)
        facts = [row for row in rows if row.memory_type not in PREFERENCE_TYPES]
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
            embedding_started = perf_counter()
            try:
                diagnostics["embedding_calls"] += 1
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
            finally:
                diagnostics["embedding_ms"] = round((perf_counter() - embedding_started) * 1000, 2)
        database_scores = {}
        if query_vector and cache_state.get("indexed"):
            try:
                semantic_rows, database_scores, info = await asyncio.to_thread(self._database_semantic,
                    user_id, project_id, query_vector, signature, config["dimensions"])
                unique = {row.id: row for row in rows}
                unique.update({row.id: row for row in semantic_rows if row.id not in unique})
                rows = list(unique.values())
                facts = [row for row in rows if row.memory_type not in PREFERENCE_TYPES]
                diagnostics.update(info, read_rows=len(rows))
            except SQLAlchemyError:
                diagnostics["fallback_reason"] = "semantic_database_unavailable"
        # 普通回答先选少量候选，再用 PG 重新校验；不重复搬运整批向量正文。
        semantic, ranking, hashes, candidate_ids = await asyncio.to_thread(
            self._rank, rows, facts, cached, query_vector, query, user_id, project_id, database_scores, turn_query=turn_query)
        diagnostics["snapshot_hashes"] = hashes
        return await asyncio.to_thread(self._finalize, user_id, project_id, query, max_chars,
            signature, generated, semantic, ranking, diagnostics, cache_state, candidate_ids, turn_query=turn_query)
