from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.services.tools.query_rewriter import QueryRewriteService


class QueryRewriteServiceTest(unittest.TestCase):
    def test_rewrites_coreference_distance_query_from_recent_places(self) -> None:
        service = QueryRewriteService()
        recent_messages = [
            SimpleNamespace(role="user", content="帮我看看深圳松岗、广州南站这两个地方。"),
            SimpleNamespace(role="assistant", content="深圳松岗位于深圳市宝安区，广州南站位于广州市番禺区。"),
        ]

        result = service.rewrite(
            query="他们离汕头市潮阳区西凤村多远",
            recent_messages=recent_messages,
        )

        self.assertTrue(result.did_rewrite)
        self.assertIn("汕头市潮阳区西凤村", result.rewritten_query)
        self.assertIn("深圳松岗", result.rewritten_query)
        self.assertIn("广州南站", result.rewritten_query)

    def test_does_not_rewrite_without_coreference(self) -> None:
        service = QueryRewriteService()

        result = service.rewrite(
            query="深圳松岗离汕头市潮阳区西凤村多远",
            recent_messages=[],
        )

        self.assertFalse(result.did_rewrite)
        self.assertEqual(result.rewritten_query, "深圳松岗离汕头市潮阳区西凤村多远")

    def test_prefers_user_places_over_assistant_generated_locations(self) -> None:
        service = QueryRewriteService()
        result = service.rewrite(
            query="他们离汕头市潮阳区西凤村多远",
            recent_messages=[
                SimpleNamespace(role="user", content="深圳松岗和广州南站这两个地点。"),
                SimpleNamespace(role="assistant", content="还可以考虑北京西站和上海虹桥站。"),
            ],
        )

        self.assertTrue(result.did_rewrite)
        self.assertIn("深圳松岗", result.rewritten_query)
        self.assertIn("广州南站", result.rewritten_query)
        self.assertNotIn("北京西站", result.rewritten_query)


if __name__ == "__main__":
    unittest.main()
