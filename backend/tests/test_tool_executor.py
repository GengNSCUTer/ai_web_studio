from __future__ import annotations

import asyncio
import unittest
from app.services.tools.catalog import ToolCatalog
from app.services.tools.credentials import ToolCredential, ToolCredentialResolver
from app.services.tools.executor import ToolExecutor
from app.services.tools.schemas import ExternalSource, PlannedToolCall, ToolDefinition, ToolExecutionFeedbackError


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


class FailingAdapterRunner:
    async def run(self, *, definition, call, api_key):
        raise RuntimeError("https://mcp.internal/call?api_key=secret-value")


class FeedbackAdapterRunner:
    async def run(self, *, definition, call, api_key):
        raise ToolExecutionFeedbackError("old_string 出现 2 次，请提供更多上下文。")


class DisabledCredentialResolver(FakeCredentialResolver):
    def resolve(self, *, user_id: str | None, provider_key: str) -> ToolCredential:
        return ToolCredential(provider_key=provider_key, api_key=None, source="missing", is_enabled=False)


class FullWorkspaceCredentialResolver(FakeCredentialResolver):
    def get_workspace_permission_mode(self, *, project_id: str | None) -> str:
        return "full_workspace"


class ToolExecutorTest(unittest.TestCase):
    def test_public_tool_call_redacts_sensitive_arguments(self) -> None:
        call = PlannedToolCall(
            call_id="call-secret",
            tool_key="custom.tool",
            provider="custom",
            category="custom",
            display_name="Custom",
            confidence=1.0,
            reason="test",
            arguments={"query": "safe", "api_key": "must-not-persist", "nested": {"access_token": "hidden"}},
        )

        public = call.to_public_dict()

        self.assertEqual(public["arguments"]["query"], "safe")
        self.assertEqual(public["arguments"]["api_key"], "***")
        self.assertEqual(public["arguments"]["nested"]["access_token"], "***")

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

    def test_full_workspace_does_not_bypass_arbitrary_external_write(self) -> None:
        async def run_test() -> None:
            catalog = ToolCatalog()
            definition = catalog.get("web.tavily.search")
            definition.risk_level = "high"
            definition.read_only = False
            adapter_runner = FakeAdapterRunner()
            executor = ToolExecutor(
                credential_resolver=FullWorkspaceCredentialResolver(),
                catalog=catalog,
                adapter_runner=adapter_runner,
                project_id="workspace-1",
            )
            call = PlannedToolCall(
                call_id="call-external-write",
                tool_key="web.tavily.search",
                provider="tavily",
                category="external_write",
                display_name="External write",
                confidence=0.9,
                reason="must remain blocked",
                arguments={"query": "write"},
            )

            result, events = await executor.execute(call)

            self.assertEqual(result.status, "skipped")
            self.assertIsNone(adapter_runner.call)
            confirmation = [event for event in events if event.type == "tool_confirmation_required"][0]
            self.assertEqual(confirmation.payload["permission_mode"], "full_workspace")
            self.assertEqual(confirmation.payload["status"], "blocked")

        asyncio.run(run_test())

    def test_executor_does_not_expose_adapter_exception_text_in_trace(self) -> None:
        async def run_test() -> None:
            executor = ToolExecutor(
                credential_resolver=FakeCredentialResolver(),
                catalog=ToolCatalog(),
                adapter_runner=FailingAdapterRunner(),
            )
            call = PlannedToolCall(
                call_id="call-error",
                tool_key="web.tavily.search",
                provider="tavily",
                category="web_search",
                display_name="Tavily 搜索",
                confidence=0.9,
                reason="test",
                arguments={"query": "AI news"},
            )

            result, events = await executor.execute(call)

            self.assertEqual(result.status, "error")
            self.assertIn("调用失败", result.error_message or "")
            serialized = " ".join(str(event.payload) for event in events)
            self.assertNotIn("mcp.internal", serialized)
            self.assertNotIn("secret-value", serialized)

        asyncio.run(run_test())

    def test_executor_preserves_sanitized_tool_feedback(self) -> None:
        async def run_test() -> None:
            executor = ToolExecutor(
                credential_resolver=FakeCredentialResolver(),
                catalog=ToolCatalog(),
                adapter_runner=FeedbackAdapterRunner(),
            )
            call = PlannedToolCall(
                call_id="call-feedback",
                tool_key="workspace.files.propose_edit",
                provider="workspace",
                category="workspace_file",
                display_name="编辑预览",
                confidence=0.9,
                reason="test feedback",
                arguments={"file_id": "file", "old_string": "x", "new_string": "y"},
            )

            result, events = await executor.execute(call)

            self.assertEqual(result.status, "error")
            self.assertIn("出现 2 次", result.error_message or "")
            error_event = [event for event in events if event.type == "tool_call_error"][-1]
            self.assertEqual(error_event.payload["error_kind"], "tool_feedback")

        asyncio.run(run_test())

    def test_no_auth_mcp_tool_executes_without_credential(self) -> None:
        async def run_test() -> None:
            catalog = ToolCatalog()
            definition = ToolDefinition(
                tool_key="mcp.public.weather",
                provider="public",
                category="weather",
                display_name="Public Weather",
                description="No-auth MCP weather tool",
                input_schema={"type": "object", "properties": {"city": {"type": "string"}}},
                adapter_type="mcp_http",
                adapter={
                    "endpoint_template": "https://example.test/mcp",
                    "mcp_tool_name": "weather",
                    "auth_type": "none",
                },
                source_type="mcp_server",
                risk_level="low",
                read_only=True,
            )
            catalog._definitions = {definition.tool_key: definition}
            adapter_runner = FakeAdapterRunner()
            executor = ToolExecutor(
                credential_resolver=DisabledCredentialResolver(),
                catalog=catalog,
                adapter_runner=adapter_runner,
            )
            call = PlannedToolCall(
                call_id="call-public",
                tool_key=definition.tool_key,
                provider=definition.provider,
                category=definition.category,
                display_name=definition.display_name,
                confidence=0.9,
                reason="test no-auth execution",
                arguments={"city": "深圳"},
            )

            result, events = await executor.execute(call)

            self.assertEqual(result.status, "success")
            self.assertIsNone(adapter_runner.api_key)
            policy = [event for event in events if event.type == "tool_policy_check"][-1]
            self.assertEqual(policy.payload["credential_source"], "not_required")
            self.assertFalse(policy.payload["credential_required"])

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
