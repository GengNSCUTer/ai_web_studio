from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from app.services.knowledge_error import KnowledgeRetrievalUnavailableError
from app.schemas.knowledge import KnowledgeEvalRunRequest
from app.services.knowledge_evaluation_service import KnowledgeEvalOutcome, KnowledgeEvaluationService
from app.services.knowledge_index_service import RetrievalResult
from app.services.knowledge_retrieval_pipeline import KnowledgeRetrievalFilter, KnowledgeRetrievalPipeline


def _knowledge_base(**overrides: object) -> SimpleNamespace:
    values = {
        "id": "kb-1",
        "active_index_generation": "generation-1",
        "retrieval_mode": "hybrid",
        "rerank_enabled": False,
        "rerank_top_n": 3,
        "score_threshold": 0.0,
        "rerank_model": "test-reranker",
        "embedding_dimensions": 3,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _result(chunk_id: str, *, source: str = "vector") -> RetrievalResult:
    chunk = SimpleNamespace(
        id=chunk_id,
        document_id=f"doc-{chunk_id}",
        content=f"content-{chunk_id}",
        metadata_json="{}",
    )
    return RetrievalResult(
        chunk=chunk,
        score=0.9,
        rank_source=source,
        metadata={},
    )


class KnowledgeRetrievalPipelineFallbackTest(unittest.TestCase):
    def test_hybrid_keeps_vector_results_when_lexical_channel_fails(self) -> None:
        pipeline = KnowledgeRetrievalPipeline(chunk_repo=Mock(), setting_service=Mock())
        vector_result = _result("vector-hit")

        async def vector_results(**_: object) -> list[RetrievalResult]:
            return [vector_result]

        pipeline._retrieve_vector_results = vector_results  # type: ignore[method-assign]
        pipeline._retrieve_lexical_results = Mock(side_effect=RuntimeError("secret lexical endpoint"))  # type: ignore[method-assign]

        results = asyncio.run(
            pipeline.retrieve_async(
                user_id="user-1",
                knowledge_base=_knowledge_base(),
                query="hybrid fallback",
                top_k=3,
            )
        )

        self.assertEqual([item.chunk.id for item in results], ["vector-hit"])
        metadata = results[0].metadata
        self.assertTrue(metadata["hybrid_fallback"])
        self.assertEqual(metadata["hybrid_fallback_reason"], "lexical_unavailable")
        self.assertEqual(metadata["lexical_error_code"], "knowledge_retrieval_failed")
        self.assertNotIn("secret lexical endpoint", str(metadata))

    def test_hybrid_keeps_lexical_results_when_vector_channel_fails(self) -> None:
        pipeline = KnowledgeRetrievalPipeline(chunk_repo=Mock(), setting_service=Mock())
        lexical_result = _result("lexical-hit", source="lexical")

        async def vector_results(**_: object) -> list[RetrievalResult]:
            raise RuntimeError("secret embedding endpoint")

        pipeline._retrieve_vector_results = vector_results  # type: ignore[method-assign]
        pipeline._retrieve_lexical_results = Mock(return_value=[lexical_result])  # type: ignore[method-assign]

        results = asyncio.run(
            pipeline.retrieve_async(
                user_id="user-1",
                knowledge_base=_knowledge_base(),
                query="hybrid fallback",
                top_k=3,
            )
        )

        self.assertEqual([item.chunk.id for item in results], ["lexical-hit"])
        metadata = results[0].metadata
        self.assertEqual(metadata["hybrid_fallback_reason"], "vector_unavailable")
        self.assertEqual(metadata["vector_error_code"], "knowledge_retrieval_failed")
        self.assertNotIn("secret embedding endpoint", str(metadata))

    def test_hybrid_raises_stable_error_when_both_channels_fail(self) -> None:
        pipeline = KnowledgeRetrievalPipeline(chunk_repo=Mock(), setting_service=Mock())

        async def vector_results(**_: object) -> list[RetrievalResult]:
            raise RuntimeError("vector provider secret")

        pipeline._retrieve_vector_results = vector_results  # type: ignore[method-assign]
        pipeline._retrieve_lexical_results = Mock(side_effect=RuntimeError("lexical provider secret"))  # type: ignore[method-assign]

        with self.assertRaises(KnowledgeRetrievalUnavailableError):
            asyncio.run(
                pipeline.retrieve_async(
                    user_id="user-1",
                    knowledge_base=_knowledge_base(),
                    query="both unavailable",
                    top_k=3,
                )
            )

    def test_empty_rerank_response_is_marked_as_fallback(self) -> None:
        rerank_service = Mock()
        rerank_service.rerank = AsyncMock(return_value=[])
        pipeline = KnowledgeRetrievalPipeline(
            chunk_repo=Mock(),
            setting_service=Mock(),
            rerank_service=rerank_service,
        )

        results = asyncio.run(
            pipeline._rerank_if_needed(
                user_id="user-1",
                knowledge_base=_knowledge_base(rerank_enabled=True),
                query="empty rerank",
                results=[_result("hit"), _result("second")],
            )
        )

        self.assertTrue(results[0].metadata["rerank_fallback"])
        self.assertEqual(results[0].metadata["rerank_fallback_reason"], "empty_response")
        self.assertEqual(results[0].metadata["rerank_error_code"], "empty_response")


class KnowledgeEvaluationMatrixTest(unittest.TestCase):
    def test_matrix_uses_fixed_ablation_configurations(self) -> None:
        service = KnowledgeEvaluationService(
            base_repo=Mock(),
            chunk_repo=Mock(),
            eval_set_repo=Mock(),
            eval_case_repo=Mock(),
            eval_run_repo=Mock(),
            eval_result_repo=Mock(),
            setting_service=Mock(),
        )
        calls: list[KnowledgeEvalRunRequest] = []

        def fake_run_eval(*args: object) -> KnowledgeEvalOutcome:
            payload = args[-1]
            assert isinstance(payload, KnowledgeEvalRunRequest)
            calls.append(payload)
            run = SimpleNamespace(
                id=f"run-{len(calls)}",
                status="succeeded",
                retrieval_mode=payload.retrieval_mode,
                rerank_enabled=bool(payload.rerank_enabled),
                metrics={"hit_at_k": 1.0},
            )
            return KnowledgeEvalOutcome(run=run, results=[])  # type: ignore[arg-type]

        service.run_eval = fake_run_eval  # type: ignore[method-assign]
        outcome = service.run_eval_matrix("kb-1", "set-1", "user-1", top_k=5)

        self.assertIsNotNone(outcome)
        assert outcome is not None
        self.assertEqual(
            list(outcome.comparison),
            ["vector", "lexical", "hybrid", "vector_rerank", "hybrid_rerank"],
        )
        self.assertEqual(
            [(item.retrieval_mode, item.rerank_enabled, item.top_k) for item in calls],
            [
                ("vector", False, 5),
                ("lexical", False, 5),
                ("hybrid", False, 5),
                ("vector", True, 5),
                ("hybrid", True, 5),
            ],
        )


class KnowledgeRerankCandidatePoolTest(unittest.TestCase):
    def pipeline(self):
        reranker=Mock()
        async def rerank(**kwargs):
            # 选取原融合截断之外的证据，验证它确实到达精排模型。
            index=len(kwargs["documents"])-1
            return [(index,.95)]
        reranker.rerank=AsyncMock(side_effect=rerank)
        pipeline=KnowledgeRetrievalPipeline(chunk_repo=Mock(),setting_service=Mock(),rerank_service=reranker)
        pipeline._retrieve_vector_results=AsyncMock(return_value=[_result(f"v{i}") for i in range(4)])
        pipeline._retrieve_lexical_results=Mock(return_value=[_result(f"l{i}",source="lexical") for i in range(4)])
        return pipeline,reranker

    def test_reranker_sees_both_channel_candidates_before_final_cutoff(self):
        pipeline,reranker=self.pipeline()
        results=asyncio.run(pipeline.retrieve_async(user_id="user-1",knowledge_base=_knowledge_base(rerank_enabled=True),query="query",top_k=2))
        self.assertEqual(pipeline._retrieve_vector_results.call_args.kwargs["top_k"],4)
        self.assertEqual(pipeline._retrieve_lexical_results.call_args.kwargs["top_k"],4)
        self.assertEqual(len(reranker.rerank.call_args.kwargs["documents"]),8)
        self.assertEqual(results[0].chunk.id,"l3")
        self.assertEqual(results[0].metadata["rerank_candidate_count"],8)
        self.assertEqual(reranker.rerank.call_args.kwargs["top_n"],2)

    def test_rerank_timeout_does_not_return_expanded_pool(self):
        pipeline,reranker=self.pipeline()
        reranker.rerank.side_effect=TimeoutError("private provider details")
        results=asyncio.run(pipeline.retrieve_async(user_id="user-1",knowledge_base=_knowledge_base(rerank_enabled=True),query="query",top_k=2))
        self.assertEqual(len(results),2)
        self.assertTrue(all(r.metadata.get("rerank_fallback") for r in results))
        self.assertNotIn("private provider",str([r.metadata for r in results]))

    def test_disabled_rerank_keeps_requested_channel_budget(self):
        pipeline,reranker=self.pipeline()
        results=asyncio.run(pipeline.retrieve_async(user_id="user-1",knowledge_base=_knowledge_base(),query="query",top_k=2))
        self.assertEqual(pipeline._retrieve_vector_results.call_args.kwargs["top_k"],2)
        self.assertEqual(len(results),2)
        reranker.rerank.assert_not_awaited()

    def test_small_result_limit_is_respected_after_expansion(self):
        pipeline,reranker=self.pipeline()
        asyncio.run(pipeline.retrieve_async(user_id="user-1",knowledge_base=_knowledge_base(rerank_enabled=True,rerank_top_n=6),query="query",top_k=1))
        self.assertEqual(reranker.rerank.call_args.kwargs["top_n"],1)

    def test_hybrid_candidate_budget_is_bounded(self):
        pipeline,reranker=self.pipeline()
        pipeline._retrieve_vector_results.return_value=[_result(f"v{i}") for i in range(100)]
        pipeline._retrieve_lexical_results.return_value=[_result(f"l{i}",source="lexical") for i in range(100)]
        asyncio.run(pipeline.retrieve_async(user_id="user-1",knowledge_base=_knowledge_base(rerank_enabled=True),query="query",top_k=50))
        self.assertEqual(pipeline._retrieve_vector_results.call_args.kwargs["top_k"],50)
        self.assertEqual(len(reranker.rerank.call_args.kwargs["documents"]),100)

    def test_metadata_filter_keeps_existing_overfetch_without_rerank(self):
        pipeline,_=self.pipeline()
        filters=KnowledgeRetrievalFilter(document_ids=["doc-v1"])
        asyncio.run(pipeline.retrieve_async(user_id="user-1",knowledge_base=_knowledge_base(),query="query",top_k=20,filters=filters))
        self.assertEqual(pipeline._retrieve_vector_results.call_args.kwargs["top_k"],100)
        self.assertIs(pipeline._retrieve_vector_results.call_args.kwargs["filters"],filters)


if __name__ == "__main__":
    unittest.main()
