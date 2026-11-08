"""可选语义精排的预算、异常及向量完整性验证，不调用真实模型。"""

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from app.services.artifact_semantic_ranker import ArtifactSemanticRanker


class ArtifactSemanticRankerTest(unittest.IsolatedAsyncioTestCase):
    async def test_similar_preview_ranks_first_and_secret_text_is_redacted(self):
        service = SimpleNamespace(embed_texts=AsyncMock(return_value=[[1.,0.],[1.,0.],[0.,1.]]))
        ranker = ArtifactSemanticRanker(embedding_service=service)
        ranker._settings = lambda _: ("siliconflow", "test", None)
        scores, mode = await ranker.rank(user_id="u", query="API_KEY=secret-value", previews=[("a","同义资料"),("b","无关")])
        self.assertGreater(scores["a"], scores["b"])
        self.assertEqual(mode, "online_semantic_and_local_terms")
        self.assertNotIn("secret-value", service.embed_texts.call_args.kwargs["texts"][0])

    async def test_invalid_embedding_falls_back_without_accepting_wrong_scores(self):
        service = SimpleNamespace(embed_texts=AsyncMock(return_value=[[1.,0.],[float("nan"),0.]]))
        ranker = ArtifactSemanticRanker(embedding_service=service)
        ranker._settings = lambda _: ("siliconflow", "test", None)
        scores, mode = await ranker.rank(user_id="u", query="查询", previews=[("a","资料")])
        self.assertEqual(scores, {})
        self.assertEqual(mode, "semantic_unavailable_local_fallback")

    async def test_no_online_config_does_not_start_local_model(self):
        ranker = ArtifactSemanticRanker()
        ranker._settings = lambda _: None
        scores, mode = await ranker.rank(user_id="u", query="查询", previews=[("a","资料")])
        self.assertEqual((scores, mode), ({}, "online_embedding_not_configured"))
