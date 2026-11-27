import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.services.evidence_context import EvidenceSection, EvidenceUnit
from app.services.knowledge_claim_review import review_claims
from app.services.knowledge_index_service import RetrievalResult
from app.services.knowledge_lexical_cache import LexicalIndexCache
from app.services.knowledge_parent_context import parent_excerpt, unique_parent_results


class KnowledgeBoundariesTest(unittest.TestCase):
    def test_bm25_parallel_cold_load_and_atomic_legacy_replacement(self):
        with TemporaryDirectory() as root:
            path = Path(root) / "lexical_index.json"
            path.write_text('{"version": 1, "count": 2}')
            cache = LexicalIndexCache(max_entries=1)
            with patch("app.services.knowledge_lexical_cache.json.load", wraps=json.load) as load:
                with ThreadPoolExecutor(8) as pool:
                    values = list(pool.map(lambda _: cache.load(path, 1), range(16)))
                self.assertTrue(all(value is values[0] for value in values))
                self.assertEqual(load.call_count, 1)
                temporary = path.with_suffix(".tmp")
                temporary.write_text('{"version": 1, "count": 3}')
                temporary.replace(path)
                self.assertEqual(cache.load(path, 1)["count"], 3)
                self.assertEqual(len(cache.entries), 1)

    def test_parent_sections_expose_only_complete_retained_child_ranges(self):
        beginning, ending = "Redis 规定超时 30 秒。", "Kafka 规定重试最多 3 次。"
        parent = beginning + "背景。" * 120 + ending
        metadata = {"parent_index": 2, "parent_content": parent, "chunk_mode": "parent_child", "parent_source_start": 100}
        results = [RetrievalResult(SimpleNamespace(id=identifier, document_id="doc", knowledge_base_id="kb",
            content=content, source_start=100 + parent.index(content)), .9, metadata)
            for identifier, content in (("first", beginning), ("last", ending))]
        merged, = unique_parent_results(results)
        body, retained = parent_excerpt(merged, 80, anchors=("redis", "kafka"))
        self.assertEqual(body, f"{beginning}\n…\n{ending}")
        unit = EvidenceUnit({"metadata": retained, "citation_label": "[KB1]"}, "[KB1]", body, len(parent))
        compact = replace(unit, body=beginning).public_source()
        self.assertEqual(compact["metadata"]["retained_child_ids"], ["first"])
        self.assertEqual(len(compact["metadata"]["retained_ranges"]), 1)

    def test_coverage_reserves_second_term_before_long_first_evidence(self):
        first = "Redis 缓存有明确的过期策略。" * 100
        second = "Kafka 保留分区日志。" * 14
        evidence = EvidenceSection("knowledge_context", "", tuple(EvidenceUnit(
            {"metadata": {}, "citation_label": f"[KB{i}]"}, f"[KB{i}]", body, len(body))
            for i, body in enumerate((first, second), 1)), ("redis", "kafka"))
        section = evidence.fit(lambda item: len(item.render()) <= 560)
        self.assertEqual([unit.label for unit in section.units], ["[KB1]", "[KB2]"])
        self.assertLessEqual(len(section.render()), 560)

    def test_claim_judge_uses_only_local_valid_labels_and_visible_text(self):
        def judge(claims, evidence):
            self.assertEqual([claim["id"] for claim in claims], [2])
            self.assertEqual(evidence, {"[KB2]": "Only the retained sentence."})
            return {"verdicts": [{"id": 2, "verdict": "contradicted"}]}

        result = review_claims("先给一个未引用的说法。 Next claim is wrong.[KB2]", [
            {"label": "KB2", "content": "Only the retained sentence."}, {"label": "KB3", "content": "unrelated"}], judge)
        self.assertEqual([claim["verdict"] for claim in result["claims"]], ["missing_citation", "contradicted"])
        self.assertEqual(result["citation_coverage"], .5)

    def test_global_rerank_once_uses_common_model_and_keeps_source_log_ids(self):
        import asyncio
        from unittest.mock import AsyncMock
        from app.services.knowledge_context_service import KnowledgeContextResult, KnowledgeContextService

        def partial(key, score):
            chunk = SimpleNamespace(id=key, knowledge_base_id=key, document_id=key, chunk_index=0,
                source_start=0, source_end=20, content=f"Evidence for {key}.")
            return KnowledgeContextResult(None, candidates=(RetrievalResult(chunk, score, {}),),
                base=SimpleNamespace(id=key, name=key, rerank_enabled=True, max_context_chunks=2, max_context_chars=1000),
                retrieval_log_id=f"log-{key}")

        service = object.__new__(KnowledgeContextService)
        service.user_id = "user"
        service._db_call = AsyncMock(return_value=SimpleNamespace(knowledge_keys={"rerank": "fixture"},
            config=SimpleNamespace(knowledge_rerank_provider="api", knowledge_rerank_model="common-model")))
        parts = [partial("kb-a", 1.0), partial("kb-b", .32)]
        path = "app.services.knowledge_context_service.KnowledgeRerankService.rerank"
        with patch(path, new_callable=AsyncMock, return_value=[(1, .91), (0, .1)]) as rerank:
            selected, mode = asyncio.run(service._rank_multi(parts, "question", timeout=1))
            self.assertEqual(mode, "global_rerank")
            self.assertEqual([s.metadata["retrieval_log_id"] for s in selected], ["log-kb-b", "log-kb-a"])
            rerank.assert_awaited_once()
            self.assertEqual(rerank.call_args.kwargs["knowledge_base"].rerank_model, "common-model")
        with patch(path, new_callable=AsyncMock, side_effect=TimeoutError):
            selected, mode = asyncio.run(service._rank_multi(parts, "question", timeout=1))
            self.assertEqual(mode, "local_rank_interleave")
            self.assertEqual([s.metadata["retrieval_log_id"] for s in selected], ["log-kb-a", "log-kb-b"])

    def test_hybrid_discards_weak_vector_candidates_before_rrf_and_rerank(self):
        import asyncio
        from unittest.mock import AsyncMock, Mock
        from app.services.knowledge_retrieval_pipeline import KnowledgeRetrievalPipeline

        vector = Mock()
        vector.search.return_value = [SimpleNamespace(chunk=SimpleNamespace(id=str(i), metadata_json="{}"), score=score)
            for i, score in enumerate((.23, .19, .15))]
        embed = AsyncMock(return_value=[[1., 0.]])
        ranker = SimpleNamespace(rerank=AsyncMock())
        pipeline = KnowledgeRetrievalPipeline(chunk_repo=Mock(), setting_service=Mock(), vector_search=vector,
            embedding_service=SimpleNamespace(embed_texts=embed), rerank_service=ranker)
        pipeline._retrieve_lexical_results = AsyncMock(return_value=[])
        base = SimpleNamespace(id="kb", active_index_generation="legacy", retrieval_mode="hybrid", rerank_enabled=True,
            rerank_top_n=3, embedding_dimensions=2, embedding_provider="fixture", embedding_model="mock", score_threshold=.6)
        self.assertEqual(asyncio.run(pipeline.retrieve_async(user_id="u", knowledge_base=base, query="question", top_k=3)), [])
        self.assertEqual((pipeline.diagnostics["vector_candidates"], pipeline.diagnostics["vector_passed"]), (3, 0))
        ranker.rerank.assert_not_awaited()
