from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

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

    def test_model_rewrites_explicit_former_reference(self) -> None:
        provider = AsyncMock()
        provider.complete_chat.return_value = (
            '{"resolved": true, "standalone_query": "Adaptive-RAG 如何选择检索策略？"}'
        )

        result = __import__("asyncio").run(
            self.service.rewrite_async(
                query="它如何选择检索策略？",
                recent_messages=[
                    {"id": "previous", "role": "user", "content": "Adaptive-RAG 的检索策略是什么？"},
                    {"id": "assistant", "role": "assistant", "content": "不要用这条回答。"},
                ],
                provider_type="openai-compatible",
                base_url="https://example.invalid/v1",
                api_key="test-key",
                model_name="test-model",
                provider_service=provider,
            )
        )

        self.assertEqual(result.rewritten_query, "Adaptive-RAG 如何选择检索策略？")
        self.assertEqual(result.strategy, "llm_standalone_query_v1")
        self.assertEqual(result.context_message_id, "previous")
        sent_messages = provider.complete_chat.call_args.kwargs["messages"]
        self.assertNotIn("不要用这条回答", str(sent_messages))

    def test_ambiguous_bare_reference_is_not_guessed(self) -> None:
        provider = AsyncMock()
        result = __import__("asyncio").run(
            self.service.rewrite_async(
                query="它如何触发检索？",
                recent_messages=[{"role": "user", "content": "Adaptive-RAG 与 FLARE 有什么不同？"}],
                provider_type="openai-compatible",
                base_url="https://example.invalid/v1",
                api_key="test-key",
                model_name="test-model",
                provider_service=provider,
            )
        )
        self.assertFalse(result.did_rewrite)
        provider.complete_chat.assert_not_called()

    def test_explicit_latter_reference_uses_deterministic_entity_replacement(self) -> None:
        provider = AsyncMock()
        result = __import__("asyncio").run(
            self.service.rewrite_async(
                query="后者为什么需要多样性检索？",
                recent_messages=[
                    {"id": "previous", "role": "user", "content": "Adaptive-RAG 和 DF-RAG 都与检索有关。"}
                ],
                provider_type="openai-compatible",
                base_url="https://example.invalid/v1",
                api_key="test-key",
                model_name="test-model",
                provider_service=provider,
            )
        )
        self.assertTrue(result.did_rewrite)
        self.assertEqual(result.rewritten_query, "DF-RAG为什么需要多样性检索？")
        self.assertEqual(result.strategy, "deterministic_reference_resolution_v1")
        provider.complete_chat.assert_not_called()

    def test_model_failure_keeps_original_query(self) -> None:
        for response in ("not-json", '{"resolved": false, "standalone_query": "X"}'):
            with self.subTest(response=response):
                provider = AsyncMock()
                provider.complete_chat.return_value = response
                result = __import__("asyncio").run(
                    self.service.rewrite_async(
                        query="它如何触发检索？",
                        recent_messages=[{"role": "user", "content": "Adaptive-RAG 的检索触发逻辑。"}],
                        provider_type="openai-compatible",
                        base_url="https://example.invalid/v1",
                        api_key="test-key",
                        model_name="test-model",
                        provider_service=provider,
                    )
                )
                self.assertFalse(result.did_rewrite)
                self.assertEqual(result.rewritten_query, "它如何触发检索？")

    def test_missing_provider_keeps_original_query(self) -> None:
        result = __import__("asyncio").run(
            self.service.rewrite_async(
                query="它如何触发检索？",
                recent_messages=[{"role": "user", "content": "Adaptive-RAG 的检索触发逻辑。"}],
            )
        )
        self.assertFalse(result.did_rewrite)


if __name__ == "__main__":
    unittest.main()
