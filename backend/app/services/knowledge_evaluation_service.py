from __future__ import annotations

import json
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
from app.services.knowledge_vector_search import CURRENT_EMBEDDING_VERSION
from app.services.setting_service import SettingService


@dataclass(frozen=True)
class KnowledgeEvalOutcome:
    run: KnowledgeEvalRunResponse
    results: list[KnowledgeEvalResultResponse]


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

        expected_document_id = payload.expected_document_id
        if payload.expected_chunk_id:
            expected_chunk = self.chunk_repo.get_by_user(payload.expected_chunk_id, user_id)
            if not expected_chunk or expected_chunk.knowledge_base_id != knowledge_base_id:
                raise ValueError("期望 Chunk 不存在或不属于当前知识库。")
            if expected_document_id and expected_document_id != expected_chunk.document_id:
                raise ValueError("期望 Chunk 与期望文档不属于同一份文档。")
            # 精确 Chunk 会在重索引时失效；同时保存稳定一些的文档级目标，供 SET NULL 后继续评测。
            expected_document_id = expected_chunk.document_id
        elif expected_document_id:
            expected_document = self.document_repo.get_by_user(expected_document_id, user_id)
            if not expected_document or expected_document.knowledge_base_id != knowledge_base_id:
                raise ValueError("期望文档不存在或不属于当前知识库。")

        item = KnowledgeEvalCase(
            user_id=user_id,
            knowledge_base_id=knowledge_base_id,
            eval_set_id=eval_set_id,
            query=payload.query.strip(),
            expected_document_id=expected_document_id,
            expected_chunk_id=payload.expected_chunk_id,
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
            total_elapsed_ms = 0.0
            failure_count = 0
            fallback_count = 0
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
                    case_errors.append({"case_id": case.id, "error": str(exc)})
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
                metrics = self._score_case(
                    retrieved=retrieved,
                    expected_chunk_id=case.expected_chunk_id,
                    expected_document_id=case.expected_document_id,
                )
                hit_sum += float(metrics["hit_at_k"])
                mrr_sum += float(metrics["mrr"] or 0.0)
                precision_sum += float(metrics["context_precision"] or 0.0)
                recall_sum += float(metrics["context_recall"] or 0.0)
                result = self.eval_result_repo.create_result(
                    KnowledgeEvalResult(
                        user_id=user_id,
                        knowledge_base_id=knowledge_base_id,
                        run_id=run.id,
                        case_id=case.id,
                        query=case.query,
                        retrieved_json=self._dump_json(retrieved_payload),
                        expected_document_id=case.expected_document_id,
                        expected_chunk_id=case.expected_chunk_id,
                        hit_at_k=bool(metrics["hit_at_k"]),
                        mrr=metrics["mrr"],
                        context_precision=metrics["context_precision"],
                        context_recall=metrics["context_recall"],
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
                    "top_k": resolved_top_k,
                    "avg_elapsed_ms": total_elapsed_ms / case_count,
                    "total_elapsed_ms": total_elapsed_ms,
                    "failure_count": failure_count,
                    "fallback_count": fallback_count,
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
            run.error_message = str(exc)
            run.finished_at = datetime.now(timezone.utc)
            if not run.metrics_json:
                run.metrics_json = self._dump_dict(
                    {
                        "case_count": len(cases),
                        "failure_count": 1,
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
        expected_chunk_id: str | None,
        expected_document_id: str | None,
    ) -> dict[str, float | bool]:
        matched_index: int | None = None
        for index, item in enumerate(retrieved, start=1):
            # Chunk-level ground truth is more specific. The document target is
            # only a fallback after reindexing has SET NULL on expected_chunk_id.
            if expected_chunk_id:
                matched = item.chunk.id == expected_chunk_id
            else:
                matched = bool(expected_document_id and item.chunk.document_id == expected_document_id)
            if matched:
                matched_index = index
                break
        hit_at_k = matched_index is not None
        mrr = 1.0 / matched_index if matched_index else 0.0
        relevant_count = 1 if hit_at_k else 0
        retrieved_count = max(1, len(retrieved))
        return {
            "hit_at_k": hit_at_k,
            "mrr": mrr if hit_at_k else 0.0,
            "context_precision": relevant_count / retrieved_count,
            "context_recall": 1.0 if hit_at_k else 0.0,
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
            expected_chunk_id=item.expected_chunk_id,
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
            expected_chunk_id=item.expected_chunk_id,
            hit_at_k=item.hit_at_k,
            mrr=item.mrr,
            context_precision=item.context_precision,
            context_recall=item.context_recall,
            created_at=item.created_at,
        )
