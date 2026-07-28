from __future__ import annotations

import unittest

from app.services.knowledge_query_rewriter import KnowledgeQueryRewriteService


class KnowledgeQueryRewriteServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.service = KnowledgeQueryRewriteService()

    def test_expands_short_coreference_with_latest_user_question(self) -> None:
        result = self.service.rewrite(
            query="它为什么需要 CAS 激活？",
            recent_messages=[
                {"id": "user-1", "role": "user", "content": "请解释 KnowledgeIndexGeneration。"},
                {"id": "assistant-1", "role": "assistant", "content": "模型可能生成不可靠的解释。"},
            ],
        )

        self.assertTrue(result.did_rewrite)
        self.assertEqual(result.context_message_id, "user-1")
        self.assertEqual(
            result.rewritten_query,
            "请解释 KnowledgeIndexGeneration。；追问：它为什么需要 CAS 激活？",
        )
        self.assertEqual(result.strategy, "user_context_expansion_v1")

    def test_does_not_use_assistant_text_as_retrieval_intent(self) -> None:
        result = self.service.rewrite(
            query="它为什么需要 CAS 激活？",
            recent_messages=[
                {"id": "assistant-1", "role": "assistant", "content": "错误地说它是 Redis 锁。"},
            ],
        )

        self.assertFalse(result.did_rewrite)
        self.assertEqual(result.rewritten_query, result.original_query)

    def test_independent_query_is_not_rewritten(self) -> None:
        result = self.service.rewrite(
            query="KnowledgeIndexGeneration 为什么需要 CAS 激活？",
            recent_messages=[
                {"id": "user-1", "role": "user", "content": "请解释 Redis Stream。"},
            ],
        )

        self.assertFalse(result.did_rewrite)
        self.assertEqual(result.rewritten_query, result.original_query)

    def test_long_query_is_not_expanded(self) -> None:
        query = "这个机制如何工作？" + ("补充上下文" * 60)
        result = self.service.rewrite(
            query=query,
            recent_messages=[{"role": "user", "content": "旧问题"}],
        )

        self.assertFalse(result.did_rewrite)
        self.assertEqual(result.rewritten_query, query)


if __name__ == "__main__":
    unittest.main()
