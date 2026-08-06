from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from time import perf_counter
from typing import Any

from app.models.knowledge import KnowledgeEvalCase, KnowledgeEvalResult, KnowledgeEvalRun, KnowledgeEvalSet
from app.repositories.knowledge_repo import (
    KnowledgeBaseRepository,
    KnowledgeChunkRepository,
    KnowledgeDocumentRepository,
    KnowledgeEvalCaseRepository,
    KnowledgeEvalResultRepository,
    KnowledgeEvalRunRepository,
    KnowledgeEvalSetRepository,
)
from app.schemas.knowledge import (
    KnowledgeEvalCaseCreate,
    KnowledgeEvalCaseResponse,
    KnowledgeEvalResultResponse,
    KnowledgeEvalRunRequest,
    KnowledgeEvalRunResponse,
    KnowledgeEvalSetCreate,
    KnowledgeEvalSetResponse,
)
from app.services.knowledge_retrieval_pipeline import KnowledgeRetrievalPipeline
from app.services.knowledge_error import (
    classify_knowledge_error,
    public_knowledge_error_message,
)
from app.services.knowledge_vector_search import CURRENT_EMBEDDING_VERSION
from app.services.setting_service import SettingService


logger = logging.getLogger(__name__)


def _normalized_keywords(values: list[str]) -> list[str]:
    seen: set[str] = set()
    normalized: list[str] = []
    for value in values:
        item = str(value or "").strip()
        key = item.casefold()
        if item and key not in seen:
            seen.add(key)
            normalized.append(item)
    return normalized


def _normalized_ids(values: list[str]) -> list[str]:
    """Normalize a reviewed ID list without changing its declared order."""
    seen: set[str] = set()
    normalized: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if item and item not in seen:
            seen.add(item)
            normalized.append(item)
    return normalized


@dataclass(frozen=True)
class KnowledgeEvalOutcome:
    run: KnowledgeEvalRunResponse
    results: list[KnowledgeEvalResultResponse]


@dataclass(frozen=True)
class KnowledgeEvalMatrixOutcome:
    """Comparable retrieval runs over one immutable evaluation set."""

    eval_set_id: str
    runs: list[KnowledgeEvalRunResponse]
    comparison: dict[str, dict[str, Any]]


@dataclass(frozen=True)
class KnowledgeEvalRetrievalConfig:
    """Detached, read-only KB view used by one evaluation run.

    Evaluation overrides must not mutate the SQLAlchemy KnowledgeBase entity:
    result repositories commit once per case and would otherwise persist a
    temporary experiment setting as the knowledge base's production setting.
    """

    id: str
    active_index_generation: str
    embedding_provider: str
    embedding_model: str
    embedding_dimensions: int
    rerank_enabled: bool
    rerank_provider: str
    rerank_model: str
    retrieval_mode: str
    rerank_top_n: int
    score_threshold: float


class KnowledgeEvaluationService:
    # Keep the matrix deliberately small and deterministic.  It gives us a
    # useful ablation table without silently changing the knowledge base's
    # production retrieval settings.
    GOLD_SET_MATRIX: tuple[tuple[str, str, bool], ...] = (
        ("vector", "vector", False),
        ("lexical", "lexical", False),
        ("hybrid", "hybrid", False),
        ("vector_rerank", "vector", True),
        ("hybrid_rerank", "hybrid", True),
    )

    def __init__(
        self,
        *,
        base_repo: KnowledgeBaseRepository,
        chunk_repo: KnowledgeChunkRepository,
        eval_set_repo: KnowledgeEvalSetRepository,
        eval_case_repo: KnowledgeEvalCaseRepository,
        eval_run_repo: KnowledgeEvalRunRepository,
        eval_result_repo: KnowledgeEvalResultRepository,
        setting_service: SettingService,
        document_repo: KnowledgeDocumentRepository | None = None,
        retrieval_pipeline: KnowledgeRetrievalPipeline | None = None,
    ) -> None:
        self.base_repo = base_repo
        self.chunk_repo = chunk_repo
        self.eval_set_repo = eval_set_repo
        self.eval_case_repo = eval_case_repo
        self.eval_run_repo = eval_run_repo
        self.eval_result_repo = eval_result_repo
        self.setting_service = setting_service
        self.document_repo = document_repo or KnowledgeDocumentRepository(chunk_repo.db)
        self.retrieval_pipeline = retrieval_pipeline

    def list_eval_sets(self, knowledge_base_id: str, user_id: str) -> list[KnowledgeEvalSetResponse]:
        return [
            self._to_set_response(item)
            for item in self.eval_set_repo.list_by_knowledge_base(knowledge_base_id, user_id)
        ]

    def create_eval_set(
        self,
        knowledge_base_id: str,
        user_id: str,
        payload: KnowledgeEvalSetCreate,
    ) -> KnowledgeEvalSetResponse | None:
        if not self.base_repo.get_by_user(knowledge_base_id, user_id):
            return None
        item = KnowledgeEvalSet(
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
            name=payload.name.strip(),
            description=payload.description.strip() if payload.description else None,
        )
        return self._to_set_response(self.eval_set_repo.save(item))

    def list_eval_cases(self, eval_set_id: str, user_id: str) -> list[KnowledgeEvalCaseResponse]:
        return [self._to_case_response(item) for item in self.eval_case_repo.list_by_eval_set(eval_set_id, user_id)]

    def add_eval_case(
        self,
        knowledge_base_id: str,
        eval_set_id: str,
        user_id: str,
        payload: KnowledgeEvalCaseCreate,
    ) -> KnowledgeEvalCaseResponse | None:
        if not self.base_repo.get_by_user(knowledge_base_id, user_id):
            return None
        eval_set = self.eval_set_repo.get_by_user(eval_set_id, user_id)
        if not eval_set or eval_set.knowledge_base_id != knowledge_base_id:
            return None

        expected_document_ids = _normalized_ids(payload.expected_document_ids)
        if payload.expected_document_id and payload.expected_document_id not in expected_document_ids:
            expected_document_ids.insert(0, payload.expected_document_id)
        expected_chunk_ids = _normalized_ids(payload.expected_chunk_ids)
        if payload.expected_chunk_id and payload.expected_chunk_id not in expected_chunk_ids:
            expected_chunk_ids.insert(0, payload.expected_chunk_id)

        for expected_document_id in expected_document_ids:
            expected_document = self.document_repo.get_by_user(expected_document_id, user_id)
            if not expected_document or expected_document.knowledge_base_id != knowledge_base_id:
                raise ValueError("期望文档不存在或不属于当前知识库。")
        for expected_chunk_id in expected_chunk_ids:
            expected_chunk = self.chunk_repo.get_by_user(expected_chunk_id, user_id)
            if not expected_chunk or expected_chunk.knowledge_base_id != knowledge_base_id:
                raise ValueError("期望 Chunk 不存在或不属于当前知识库。")
            if expected_chunk.document_id not in expected_document_ids:
                expected_document_ids.append(expected_chunk.document_id)

        # 精确 Chunk 会在重索引时失效；文档列表仍保留，供文档级回退评测。
        expected_document_id = expected_document_ids[0] if expected_document_ids else None
        expected_chunk_id = expected_chunk_ids[0] if expected_chunk_ids else None

        item = KnowledgeEvalCase(
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
            eval_set_id=eval_set_id,
            query=payload.query.strip(),
            expected_document_id=expected_document_id,
            expected_document_ids_json=self._dump_list(expected_document_ids),
            expected_chunk_id=expected_chunk_id,
            expected_chunk_ids_json=self._dump_list(expected_chunk_ids),
            expected_answer_keywords_json=self._dump_list(payload.expected_answer_keywords),
            difficulty=payload.difficulty.strip() if payload.difficulty else None,
            tags_json=self._dump_list(payload.tags),
        )
        return self._to_case_response(self.eval_case_repo.save(item))

    def run_eval(
        self,
        knowledge_base_id: str,
        eval_set_id: str,
        user_id: str,
        payload: KnowledgeEvalRunRequest,
    ) -> KnowledgeEvalOutcome | None:
        knowledge_base = self.base_repo.get_by_user(knowledge_base_id, user_id)
        if not knowledge_base:
            return None
        eval_set = self.eval_set_repo.get_by_user(eval_set_id, user_id)
        if not eval_set or eval_set.knowledge_base_id != knowledge_base_id:
            return None
        cases = self.eval_case_repo.list_by_eval_set(eval_set_id, user_id)
        if not cases:
            raise ValueError("评测集还没有 Case，无法运行评测。")

        resolved_top_k = payload.top_k or knowledge_base.retrieval_top_k
        resolved_retrieval_mode = payload.retrieval_mode or knowledge_base.retrieval_mode
        resolved_rerank_enabled = (
            payload.rerank_enabled
            if payload.rerank_enabled is not None
            else bool(knowledge_base.rerank_enabled)
        )
        retrieval_config = self._build_retrieval_config(
            knowledge_base=knowledge_base,
            retrieval_mode=resolved_retrieval_mode,
            rerank_enabled=resolved_rerank_enabled,
        )
        config_snapshot = self._build_config_snapshot(
            knowledge_base=knowledge_base,
            retrieval_mode=resolved_retrieval_mode,
            top_k=resolved_top_k,
            rerank_enabled=resolved_rerank_enabled,
        )
        run = KnowledgeEvalRun(
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
            eval_set_id=eval_set_id,
            status="running",
            retrieval_mode=resolved_retrieval_mode,
            top_k=resolved_top_k,
            rerank_enabled=resolved_rerank_enabled,
            started_at=datetime.now(timezone.utc),
        )
        run = self.eval_run_repo.save(run)
        try:
            pipeline = self.retrieval_pipeline or KnowledgeRetrievalPipeline(
                chunk_repo=self.chunk_repo,
                setting_service=self.setting_service,
            )
            result_items: list[KnowledgeEvalResultResponse] = []
            hit_sum = 0.0
            mrr_sum = 0.0
            precision_sum = 0.0
            recall_sum = 0.0
            ndcg_sum = 0.0
            keyword_recall_sum = 0.0
            keyword_recall_count = 0
            total_elapsed_ms = 0.0
            failure_count = 0
            fallback_count = 0
            stale_chunk_target_count = 0
            case_errors: list[dict[str, str]] = []
            for case in cases:
                started_at = perf_counter()
                try:
                    retrieved = pipeline.retrieve(
                        user_id=user_id,
                        knowledge_base=retrieval_config,
                        query=case.query,
                        top_k=resolved_top_k,
                    )
                except Exception as exc:
                    retrieved = []
                    failure_count += 1
                    error_code = classify_knowledge_error(exc)
                    logger.exception(
                        "knowledge evaluation case failed: run_id=%s case_id=%s error_code=%s",
                        run.id,
                        case.id,
                        error_code,
                    )
                    case_errors.append(
                        {
                            "case_id": case.id,
                            "error_code": error_code,
                            "message": public_knowledge_error_message("evaluation_case_failed"),
                        }
                    )
                elapsed_ms = (perf_counter() - started_at) * 1000
                total_elapsed_ms += elapsed_ms
                if any(
                    item.metadata.get("hybrid_fallback") or item.metadata.get("rerank_fallback")
                    for item in retrieved
                ):
                    fallback_count += 1
                retrieved_payload = [
                    {
                        "chunk_id": item.chunk.id,
                        "document_id": item.chunk.document_id,
                        "score": item.rerank_score if item.rerank_score is not None else item.score,
                        "vector_score": item.score,
                        "rerank_score": item.rerank_score,
                        "rank_source": item.rank_source,
                        "content": item.chunk.content,
                        "metadata": item.metadata,
                    }
                    for item in retrieved
                ]
                declared_chunk_ids = self._case_chunk_ids(case)
                active_chunk_ids = self.chunk_repo.list_existing_ids(
                    chunk_ids=declared_chunk_ids,
                    knowledge_base_id=knowledge_base_id,
                    user_id=user_id,
                    index_generation=retrieval_config.active_index_generation,
                )
                stale_chunk_target_count += len(declared_chunk_ids) - len(active_chunk_ids)
                metrics = self._score_case(
                    retrieved=retrieved,
                    # A process crash between replacing DB chunks and publishing
                    # the lexical snapshot can leave old IDs in the case JSON for
                    # a short time. Restrict targets to the active generation; if
                    # none remain, _score_case deliberately falls back to the
                    # durable document-level labels.
                    expected_chunk_ids=active_chunk_ids,
                    expected_document_ids=self._case_document_ids(case),
                    expected_answer_keywords=self._load_list(case.expected_answer_keywords_json),
                )
                hit_sum += float(metrics["hit_at_k"])
                mrr_sum += float(metrics["mrr"] or 0.0)
                precision_sum += float(metrics["context_precision"] or 0.0)
                recall_sum += float(metrics["context_recall"] or 0.0)
                ndcg_sum += float(metrics["ndcg_at_k"] or 0.0)
                if metrics["expected_keyword_recall"] is not None:
                    keyword_recall_sum += float(metrics["expected_keyword_recall"] or 0.0)
                    keyword_recall_count += 1
                result = self.eval_result_repo.create_result(
                    KnowledgeEvalResult(
                        user_id=user_id,
                        knowledge_base_id=knowledge_base_id,
                        run_id=run.id,
                        case_id=case.id,
                        query=case.query,
                        retrieved_json=self._dump_json(retrieved_payload),
                        expected_document_id=case.expected_document_id,
                        expected_document_ids_json=self._dump_list(self._case_document_ids(case)),
                        expected_chunk_id=case.expected_chunk_id,
                        expected_chunk_ids_json=self._dump_list(active_chunk_ids),
                        matched_chunk_ids_json=self._dump_list(metrics["matched_chunk_ids"]),
                        hit_at_k=bool(metrics["hit_at_k"]),
                        mrr=metrics["mrr"],
                        context_precision=metrics["context_precision"],
                        context_recall=metrics["context_recall"],
                        ndcg_at_k=metrics["ndcg_at_k"],
                        expected_keyword_recall=metrics["expected_keyword_recall"],
                        expected_keyword_hits_json=self._dump_list(metrics["expected_keyword_hits"]),
                    )
                )
                result_items.append(self._to_result_response(result))

            case_count = len(cases)
            run.status = "succeeded" if failure_count == 0 else "partial"
            run.error_message = (
                f"{failure_count} 个 Case 检索失败；详见 metrics.case_errors。"
                if failure_count
                else None
            )
            run.finished_at = datetime.now(timezone.utc)
            run.metrics_json = self._dump_dict(
                {
                    "case_count": case_count,
                    "hit_at_k": hit_sum / case_count,
                    "mrr": mrr_sum / case_count,
                    "context_precision": precision_sum / case_count,
                    "context_recall": recall_sum / case_count,
                    "ndcg_at_k": ndcg_sum / case_count,
                    "expected_keyword_recall": (
                        keyword_recall_sum / keyword_recall_count if keyword_recall_count else None
                    ),
                    "answer_correctness": {
                        "status": "not_scored",
                        "reason": "当前评测运行只执行检索和证据覆盖评测，未自动调用模型评审最终答案。",
                    },
                    "top_k": resolved_top_k,
                    "avg_elapsed_ms": total_elapsed_ms / case_count,
                    "total_elapsed_ms": total_elapsed_ms,
                    "failure_count": failure_count,
                    "fallback_count": fallback_count,
                    "stale_chunk_target_count": stale_chunk_target_count,
                    "case_errors": case_errors,
                    "config_snapshot": config_snapshot,
                }
            )
            saved_run = self.eval_run_repo.save(run)
            return KnowledgeEvalOutcome(
                run=self._to_run_response(saved_run),
                results=result_items,
            )
        except Exception as exc:
            run.status = "failed"
            error_code = classify_knowledge_error(exc)
            logger.exception(
                "knowledge evaluation run failed: run_id=%s error_code=%s",
                run.id,
                error_code,
            )
            run.error_message = public_knowledge_error_message("evaluation_failed")
            run.finished_at = datetime.now(timezone.utc)
            if not run.metrics_json:
                run.metrics_json = self._dump_dict(
                    {
                        "case_count": len(cases),
                        "failure_count": 1,
                        "error_code": "evaluation_failed",
                        "config_snapshot": config_snapshot,
                    }
                )
            saved_run = self.eval_run_repo.save(run)
            return KnowledgeEvalOutcome(
                run=self._to_run_response(saved_run),
                results=[],
            )

    def list_eval_runs(self, knowledge_base_id: str, eval_set_id: str, user_id: str) -> list[KnowledgeEvalRunResponse]:
        return [
            self._to_run_response(item)
            for item in self.eval_run_repo.list_by_eval_set(eval_set_id, user_id)
            if item.knowledge_base_id == knowledge_base_id
        ]

    def run_eval_matrix(
        self,
        knowledge_base_id: str,
        eval_set_id: str,
        user_id: str,
        *,
        top_k: int | None = None,
    ) -> KnowledgeEvalMatrixOutcome | None:
        """Run the fixed vector/BM25/hybrid/rerank comparison.

        Each configuration creates its own persisted ``KnowledgeEvalRun`` and
        configuration snapshot.  The method never mutates the production
        KnowledgeBase settings, so the resulting table is reproducible and
        safe to compare across index generations.
        """

        runs: list[KnowledgeEvalRunResponse] = []
        comparison: dict[str, dict[str, Any]] = {}
        for label, retrieval_mode, rerank_enabled in self.GOLD_SET_MATRIX:
            outcome = self.run_eval(
                knowledge_base_id,
                eval_set_id,
                user_id,
                KnowledgeEvalRunRequest(
                    top_k=top_k,
                    retrieval_mode=retrieval_mode,
                    rerank_enabled=rerank_enabled,
                ),
            )
            if outcome is None:
                return None
            runs.append(outcome.run)
            comparison[label] = {
                "run_id": outcome.run.id,
                "status": outcome.run.status,
                "retrieval_mode": outcome.run.retrieval_mode,
                "rerank_enabled": outcome.run.rerank_enabled,
                "metrics": outcome.run.metrics,
            }
        return KnowledgeEvalMatrixOutcome(
            eval_set_id=eval_set_id,
            runs=runs,
            comparison=comparison,
        )

    @staticmethod
    def _dump_list(value: list[str]) -> str:
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def _dump_json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, default=str)

    @staticmethod
    def _dump_dict(value: dict[str, Any]) -> str:
        return json.dumps(value, ensure_ascii=False, default=str)

    @staticmethod
    def _load_list(value: str | None) -> list[str]:
        if not value:
            return []
        try:
            loaded = json.loads(value)
        except json.JSONDecodeError:
            return []
        return loaded if isinstance(loaded, list) else []

    @classmethod
    def _case_document_ids(cls, case: KnowledgeEvalCase) -> list[str]:
        ids = _normalized_ids(cls._load_list(case.expected_document_ids_json))
        if case.expected_document_id and case.expected_document_id not in ids:
            ids.insert(0, case.expected_document_id)
        return ids

    @classmethod
    def _case_chunk_ids(cls, case: KnowledgeEvalCase) -> list[str]:
        ids = _normalized_ids(cls._load_list(case.expected_chunk_ids_json))
        if case.expected_chunk_id and case.expected_chunk_id not in ids:
            ids.insert(0, case.expected_chunk_id)
        return ids

    @staticmethod
    def _load_json(value: str | None) -> Any:
        if not value:
            return []
        try:
            loaded = json.loads(value)
        except json.JSONDecodeError:
            return []
        return loaded

    @staticmethod
    def _load_dict(value: str | None) -> dict[str, Any]:
        loaded = KnowledgeEvaluationService._load_json(value)
        return loaded if isinstance(loaded, dict) else {}

    @staticmethod
    def _score_case(
        *,
        retrieved: list[Any],
        expected_chunk_id: str | None = None,
        expected_document_id: str | None = None,
        expected_chunk_ids: list[str] | None = None,
        expected_document_ids: list[str] | None = None,
        expected_answer_keywords: list[str] | None = None,
    ) -> dict[str, Any]:
        expected_keywords = _normalized_keywords(expected_answer_keywords or [])
        chunk_targets = _normalized_ids(expected_chunk_ids or [])
        document_targets = _normalized_ids(expected_document_ids or [])
        if expected_chunk_id and expected_chunk_id not in chunk_targets:
            chunk_targets.insert(0, expected_chunk_id)
        if expected_document_id and expected_document_id not in document_targets:
            document_targets.insert(0, expected_document_id)
        target_ids = set(chunk_targets)
        target_documents = set(document_targets)
        matched_index: int | None = None
        relevance: list[int] = []
        matched_chunk_ids: list[str] = []
        for index, item in enumerate(retrieved, start=1):
            # Exact Chunk targets are preferred. If a reindex invalidated them,
            # document-level targets still provide a stable fallback.
            if target_ids:
                matched = item.chunk.id in target_ids
            else:
                matched = item.chunk.document_id in target_documents
            relevance.append(1 if matched else 0)
            if matched and matched_index is None:
                matched_index = index
            if matched and item.chunk.id not in matched_chunk_ids:
                matched_chunk_ids.append(item.chunk.id)
        dcg = sum(relevant / math.log2(index + 1) for index, relevant in enumerate(relevance, start=1))
        target_count = len(target_ids) if target_ids else len(target_documents)
        # With document-level fallback labels, every retrieved chunk from a
        # target document is relevant; use the observed relevant cardinality
        # for the ideal list. Exact Chunk labels retain the reviewed target
        # count and therefore measure how many distinct evidence chunks were
        # recovered.
        ideal_count = target_count if target_ids else sum(relevance)
        ideal_relevance = [1] * min(ideal_count, len(relevance)) + [0] * max(0, len(relevance) - ideal_count)
        idcg = sum(relevant / math.log2(index + 1) for index, relevant in enumerate(ideal_relevance, start=1))
        ndcg_at_k = dcg / idcg if idcg else 0.0
        keyword_hits = [
            keyword
            for keyword in expected_keywords
            if any(keyword.casefold() in str(item.chunk.content or "").casefold() for item in retrieved)
        ]
        keyword_recall = len(keyword_hits) / len(expected_keywords) if expected_keywords else None
        hit_at_k = matched_index is not None
        mrr = 1.0 / matched_index if matched_index else 0.0
        relevant_count = sum(relevance)
        retrieved_count = max(1, len(retrieved))
        context_recall = len(matched_chunk_ids) / target_count if target_count else 0.0
        return {
            "hit_at_k": hit_at_k,
            "mrr": mrr if hit_at_k else 0.0,
            "context_precision": relevant_count / retrieved_count,
            "context_recall": context_recall,
            "ndcg_at_k": ndcg_at_k,
            "expected_keyword_recall": keyword_recall,
            "expected_keyword_hits": keyword_hits,
            "matched_chunk_ids": matched_chunk_ids,
            "matched_target_count": len(matched_chunk_ids),
            "ground_truth_target_count": target_count,
        }

    @staticmethod
    def score_answer(*, answer_text: str, expected_answer_keywords: list[str]) -> dict[str, Any]:
        """Score a captured final answer without claiming semantic judge accuracy.

        This deterministic helper is intentionally separate from retrieval runs:
        it can be used when a caller has an actual answer trace, while the normal
        retrieval evaluation does not invent an answer that was never generated.
        """

        keywords = _normalized_keywords(expected_answer_keywords)
        text = str(answer_text or "").casefold()
        hits = [keyword for keyword in keywords if keyword.casefold() in text]
        recall = len(hits) / len(keywords) if keywords else None
        return {
            "keyword_precision": len(hits) / max(1, len(keywords)),
            "keyword_recall": recall,
            "matched_keywords": hits,
            "status": "scored" if keywords else "not_scored",
        }

    @staticmethod
    def _build_retrieval_config(
        *,
        knowledge_base: Any,
        retrieval_mode: str,
        rerank_enabled: bool,
    ) -> KnowledgeEvalRetrievalConfig:
        return KnowledgeEvalRetrievalConfig(
            id=knowledge_base.id,
            active_index_generation=knowledge_base.active_index_generation or "legacy",
            embedding_provider=knowledge_base.embedding_provider,
            embedding_model=knowledge_base.embedding_model,
            embedding_dimensions=knowledge_base.embedding_dimensions,
            rerank_enabled=rerank_enabled,
            rerank_provider=knowledge_base.rerank_provider,
            rerank_model=knowledge_base.rerank_model,
            retrieval_mode=retrieval_mode,
            rerank_top_n=knowledge_base.rerank_top_n,
            score_threshold=knowledge_base.score_threshold,
        )

    @staticmethod
    def _build_config_snapshot(
        *,
        knowledge_base: Any,
        retrieval_mode: str,
        top_k: int,
        rerank_enabled: bool,
    ) -> dict[str, Any]:
        return {
            "active_index_generation": knowledge_base.active_index_generation or "legacy",
            "retrieval_mode": retrieval_mode,
            "top_k": top_k,
            "score_threshold": knowledge_base.score_threshold,
            "rrf_k": KnowledgeRetrievalPipeline.RRF_K,
            "embedding": {
                "provider": knowledge_base.embedding_provider,
                "model": knowledge_base.embedding_model,
                "dimensions": knowledge_base.embedding_dimensions,
                "version": CURRENT_EMBEDDING_VERSION,
            },
            "rerank": {
                "enabled": rerank_enabled,
                "provider": knowledge_base.rerank_provider,
                "model": knowledge_base.rerank_model,
                "top_n": knowledge_base.rerank_top_n,
            },
            "chunking": {
                "mode": knowledge_base.chunk_mode,
                "size": knowledge_base.chunk_size,
                "overlap": knowledge_base.chunk_overlap,
                "parent_size": knowledge_base.parent_chunk_size,
                "child_size": knowledge_base.child_chunk_size,
                "child_overlap": knowledge_base.child_chunk_overlap,
            },
        }

    def _to_set_response(self, item: KnowledgeEvalSet) -> KnowledgeEvalSetResponse:
        return KnowledgeEvalSetResponse.model_validate(item)

    def _to_case_response(self, item: KnowledgeEvalCase) -> KnowledgeEvalCaseResponse:
        return KnowledgeEvalCaseResponse(
            id=item.id,
            user_id=item.user_id,
            knowledge_base_id=item.knowledge_base_id,
            eval_set_id=item.eval_set_id,
            query=item.query,
            expected_document_id=item.expected_document_id,
            expected_document_ids=self._case_document_ids(item),
            expected_chunk_id=item.expected_chunk_id,
            expected_chunk_ids=self._case_chunk_ids(item),
            expected_answer_keywords=self._load_list(item.expected_answer_keywords_json),
            difficulty=item.difficulty,
            tags=self._load_list(item.tags_json),
            created_at=item.created_at,
            updated_at=item.updated_at,
        )

    def _to_run_response(self, item: KnowledgeEvalRun) -> KnowledgeEvalRunResponse:
        return KnowledgeEvalRunResponse(
            id=item.id,
            user_id=item.user_id,
            knowledge_base_id=item.knowledge_base_id,
            eval_set_id=item.eval_set_id,
            status=item.status,
            retrieval_mode=item.retrieval_mode,
            top_k=item.top_k,
            rerank_enabled=item.rerank_enabled,
            metrics=self._load_dict(item.metrics_json),
            error_message=item.error_message,
            started_at=item.started_at,
            finished_at=item.finished_at,
            created_at=item.created_at,
        )

    def _to_result_response(self, item: KnowledgeEvalResult) -> KnowledgeEvalResultResponse:
        return KnowledgeEvalResultResponse(
            id=item.id,
            user_id=item.user_id,
            knowledge_base_id=item.knowledge_base_id,
            run_id=item.run_id,
            case_id=item.case_id,
            query=item.query,
            retrieved=self._load_json(item.retrieved_json),
            expected_document_id=item.expected_document_id,
            expected_document_ids=self._load_list(item.expected_document_ids_json)
            or ([item.expected_document_id] if item.expected_document_id else []),
            expected_chunk_id=item.expected_chunk_id,
            expected_chunk_ids=self._load_list(item.expected_chunk_ids_json)
            or ([item.expected_chunk_id] if item.expected_chunk_id else []),
            matched_chunk_ids=self._load_list(item.matched_chunk_ids_json),
            hit_at_k=item.hit_at_k,
            mrr=item.mrr,
            context_precision=item.context_precision,
            context_recall=item.context_recall,
            ndcg_at_k=item.ndcg_at_k,
            expected_keyword_recall=item.expected_keyword_recall,
            expected_keyword_hits=self._load_list(item.expected_keyword_hits_json),
            created_at=item.created_at,
        )
