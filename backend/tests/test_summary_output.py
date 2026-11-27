import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.services.context_governance_service import ContextBudgetPlanner
from app.services.summary_output import SUMMARY_SECTIONS, InvalidSummary, generate_summary, merge_summary_facts, render_summary
from app.services.tokenizer_service import TokenizerEstimator


def message(mid, content):
    return SimpleNamespace(id=mid, role="user", content=content)


def summary(body="无"):
    return "\n".join(f"## {title}\n{body}" for title in SUMMARY_SECTIONS)


class SummaryOutputTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.runtime = SimpleNamespace(budget=ContextBudgetPlanner.build(model_context_window=8192, context_mode="balanced"),
            tokenizer=TokenizerEstimator(), provider_type="ollama", resolved_model="test", base_url="http://localhost", provider_api_key=None)

    async def test_format_and_length_allow_one_retry_without_cutting_last_section(self):
        provider = SimpleNamespace(complete_chat=AsyncMock(side_effect=[summary("正文" * 3000), summary("后续事项")]))
        value, _ = await generate_summary(provider, self.runtime, existing=None, rows=[], facts=[], timeout=2)
        self.assertEqual(value, summary("后续事项"))
        self.assertEqual(provider.complete_chat.await_count, 2)
        provider = SimpleNamespace(complete_chat=AsyncMock(return_value="缺少小节"))
        with self.assertRaises(InvalidSummary):
            await generate_summary(provider, self.runtime, existing=None, rows=[], facts=[], timeout=2)
        self.assertEqual(provider.complete_chat.await_count, 2)

    async def test_repeated_compaction_preserves_numeric_source_quotes(self):
        source = message("msg_12", "订单接口必须保持兼容，超时为30秒，失败最多重试3次。")
        facts = merge_summary_facts([], [source])
        for version in range(20):
            other = message(f"new-{version}", "团队讨论了实施方案。")
            facts = merge_summary_facts(facts, [other])
            provider = SimpleNamespace(complete_chat=AsyncMock(return_value=summary("讨论进展")))
            text, _ = await generate_summary(provider, self.runtime, existing=summary(), rows=[other], facts=facts, timeout=2)
        rendered = render_summary(text, facts)
        self.assertIn("超时为30秒", rendered)
        self.assertIn("最多重试3次", rendered)
        self.assertTrue(all(fact["message_id"] == source.id and fact["quote"] in source.content for fact in facts))
        updated = merge_summary_facts(facts, [message("new-timeout", "订单接口超时调整为60秒。")])
        self.assertIn("超时调整为60秒", render_summary(text, updated))
        self.assertNotIn("超时为30秒", render_summary(text, updated))
