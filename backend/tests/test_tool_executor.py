from __future__ import annotations

import asyncio
import unittest
from app.services.tools.catalog import ToolCatalog
from app.services.tools.credentials import ToolCredential, ToolCredentialResolver
from app.services.tools.executor import ToolExecutor
from app.services.tools.schemas import ExternalSource, PlannedToolCall


class FakeCredentialResolver(ToolCredentialResolver):
    def __init__(self) -> None:
        pass

    def resolve(self, *, user_id: str | None, provider_key: str) -> ToolCredential:
        return ToolCredential(provider_key=provider_key, api_key="test-key", source="test", is_enabled=True)

    def is_tool_enabled_for_workspace(self, *, project_id: str | None, tool_key: str) -> bool:
        return True


class FakeAdapterRunner:
    def __init__(self) -> None:
        self.definition = None
        self.call = None
        self.api_key = None

    async def run(self, *, definition, call, api_key):
        self.definition = definition
        self.call = call
        self.api_key = api_key
        return (
            [
                ExternalSource(
                    source_type=definition.category,
                    provider=definition.provider,
                    title="测试工具结果",
                    display_text="工具结果正文",
                )
            ],
            {"adapter_type": definition.adapter_type},
        )


class ToolExecutorTest(unittest.TestCase):
    def test_executor_dispatches_by_catalog_definition(self) -> None:
        async def run_test() -> None:
            adapter_runner = FakeAdapterRunner()
            executor = ToolExecutor(
                credential_resolver=FakeCredentialResolver(),
                catalog=ToolCatalog(),
                adapter_runner=adapter_runner,
            )
            call = PlannedToolCall(
                call_id="call-1",
                tool_key="web.tavily.search",
                provider="tavily",
                category="web_search",
                display_name="Tavily 搜索",
                confidence=0.9,
                reason="test",
                arguments={"query": "AI news"},
            )

            result, events = await executor.execute(call)

            self.assertEqual(result.status, "success")
            self.assertEqual(len(result.sources), 1)
            self.assertEqual(result.sources[0].metadata["call_id"], "call-1")
            self.assertEqual(result.sources[0].metadata["tool_key"], "web.tavily.search")
            self.assertEqual(result.sources[0].metadata["tool_display_name"], "Tavily 搜索")
            self.assertEqual(adapter_runner.definition.adapter_type, "mcp_http")
            self.assertEqual(adapter_runner.api_key, "test-key")
            event_types = [event.type for event in events]
            self.assertIn("tool_policy_check", event_types)
            self.assertIn("tool_call_start", event_types)
            policy_events = [event for event in events if event.type == "tool_policy_check"]
            self.assertEqual(policy_events[-1].payload["status"], "passed")
            self.assertEqual(policy_events[-1].payload["credential_source"], "test")
            call_start = [event for event in events if event.type == "tool_call_start"][0]
            self.assertEqual(call_start.payload["adapter_type"], "mcp_http")
            self.assertEqual(events[-1].payload["adapter"]["adapter_type"], "mcp_http")

        asyncio.run(run_test())

    def test_unknown_tool_is_skipped(self) -> None:
        async def run_test() -> None:
            executor = ToolExecutor(credential_resolver=FakeCredentialResolver(), catalog=ToolCatalog())
            call = PlannedToolCall(
                call_id="call-unknown",
                tool_key="missing.tool",
                provider="missing",
                category="missing",
                display_name="Missing",
                confidence=0.1,
                reason="test",
            )

            result, events = await executor.execute(call)

            self.assertEqual(result.status, "skipped")
            self.assertIn("未知工具", events[0].payload["error"])

        asyncio.run(run_test())

    def test_high_risk_tool_requires_confirmation_and_is_not_executed(self) -> None:
        async def run_test() -> None:
            catalog = ToolCatalog()
            definition = catalog.get("web.tavily.search")
            definition.risk_level = "high"
            definition.read_only = False
            adapter_runner = FakeAdapterRunner()
            executor = ToolExecutor(
                credential_resolver=FakeCredentialResolver(),
                catalog=catalog,
                adapter_runner=adapter_runner,
            )
            call = PlannedToolCall(
                call_id="call-risk",
                tool_key="web.tavily.search",
                provider="tavily",
                category="web_search",
                display_name="Tavily 搜索",
                confidence=0.9,
                reason="test",
                arguments={"query": "AI news"},
            )

            result, events = await executor.execute(call)

            self.assertEqual(result.status, "skipped")
            self.assertIsNone(adapter_runner.call)
            event_types = [event.type for event in events]
            self.assertIn("tool_confirmation_required", event_types)
            confirmation = [event for event in events if event.type == "tool_confirmation_required"][0]
            self.assertEqual(confirmation.payload["status"], "blocked")

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
