from __future__ import annotations

import asyncio
import unittest
from app.services.tools.catalog import ToolCatalog
from app.services.tools.credentials import ToolCredential, ToolCredentialResolver
from app.services.tools.adapters import ToolAdapterRunner
from app.services.tools.executor import ToolExecutor
from app.services.tools.providers.workspace_files import WorkspaceFileToolProvider
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


class EmptyAdapterRunner:
    async def run(self, *, definition, call, api_key):
        return [], {"adapter_type": definition.adapter_type}


class EmptyAnswerClaimingAdapterRunner:
    async def run(self, *, definition, call, api_key):
        return [], {
            "adapter_type": definition.adapter_type,
            "result_semantics": "empty_answer",
        }


class InternalEmptyWorkspaceProvider(WorkspaceFileToolProvider):
    """用于验证只有项目内 Provider 类型可声明合法空答案。"""

    async def run(self, *, call):
        return [], {
            "adapter_type": "workspace_file",
            "result_semantics": "empty_answer",
        }


class ProfileAdapterRunner:
    async def run(self, *, definition, call, api_key):
        return [
            ExternalSource(
                source_type="web",
                provider="provider-a",
                title="搜索结果",
                display_text="正文证据",
                metadata={
                    "raw": {
                        "items": [
                            {
                                "name": "结果 A",
                                "href": "https://a.example",
                                "text": "正文证据",
                            }
                        ]
                    }
                },
            )
        ], {"adapter_type": definition.adapter_type}


class CoordinateMcpAdapterRunner(ToolAdapterRunner):
    async def run(self, *, definition, call, api_key):
        return [
            ExternalSource(
                source_type="map",
                provider="amap",
                title="路线",
                display_text="路线证据",
                metadata={
                    "raw": {
                        "origin": "113.324521,23.106428",
                        "destination": "113.360000,23.120000",
                        "distance": "5000",
                        "duration": "900",
                    }
                },
            )
        ], {
            "adapter_type": "mcp_http",
            "mcp_arguments": {
                "origin": "113.324521,23.106428",
                "destination": "113.360000,23.120000",
            },
        }


class UntrustedCoordinateAdapterRunner:
    async def run(self, *, definition, call, api_key):
        return await CoordinateMcpAdapterRunner().run(
            definition=definition,
            call=call,
            api_key=api_key,
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
        self.assertEqual(
            PlannedToolCall(
                call_id="call-count",
                tool_key="custom.tool",
                provider="custom",
                category="custom",
                display_name="Custom",
                confidence=1.0,
                reason="test",
                arguments={"token_count": 3},
            ).to_public_dict()["arguments"]["token_count"],
            3,
        )

    def test_source_public_payload_redacts_sensitive_metadata_text_and_url(self) -> None:
        source = ExternalSource(
            source_type="mcp",
            provider="test",
            title="secret title",
            display_text="API_KEY=secret-value token_count=3&token=another-secret",
            url="https://example.test/?access_token=secret-value&key=url-secret&next=ok",
            metadata={
                "raw": {
                    "apiKey": "secret-value",
                    "location": "1,2",
                    "content": "provider note: API_KEY=secret-value",
                    "nested": [{"excerpt": "token=another-secret"}],
                }
            },
        )

        public = source.to_public_dict()

        self.assertNotIn("secret-value", str(public))
        self.assertNotIn("another-secret", str(public))
        self.assertNotIn("url-secret", str(public))
        self.assertEqual(public["metadata"]["raw"]["apiKey"], "***")
        self.assertEqual(public["metadata"]["raw"]["location"], "1,2")
        self.assertIn("API_KEY=***", public["metadata"]["raw"]["content"])
        self.assertIn("token=***", public["metadata"]["raw"]["nested"][0]["excerpt"])
        self.assertIn("access_token=***", public["url"])
        self.assertIn("key=***", public["url"])
        self.assertIn("&next=ok", public["url"])
        self.assertIn("token_count=3", public["display_text"])
        self.assertIn("token=***", public["display_text"])

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

    def test_executor_revalidates_arguments_before_adapter_side_effect(self) -> None:
        async def run_test() -> None:
            definition = ToolDefinition(
                tool_key="test.strict",
                provider="test",
                category="lookup",
                display_name="Strict lookup",
                description="strict schema test",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
                adapter={"auth_type": "none"},
            )
            catalog = ToolCatalog()
            catalog._definitions = {definition.tool_key: definition}
            adapter_runner = FakeAdapterRunner()
            executor = ToolExecutor(
                credential_resolver=FakeCredentialResolver(),
                catalog=catalog,
                adapter_runner=adapter_runner,
            )
            call = PlannedToolCall(
                call_id="call-strict",
                tool_key=definition.tool_key,
                provider=definition.provider,
                category=definition.category,
                display_name=definition.display_name,
                confidence=1.0,
                reason="strict schema test",
                arguments={"query": "safe", "unexpected": "must be rejected"},
            )

            result, events = await executor.execute(call)

            self.assertEqual(result.status, "skipped")
            self.assertIsNone(adapter_runner.call)
            validation = [event for event in events if event.type == "tool_schema_validation"]
            self.assertEqual(validation[-1].payload["status"], "failed")

        asyncio.run(run_test())

    def test_executor_validates_required_default_and_fixed_arguments(self) -> None:
        async def run_test() -> None:
            definition = ToolDefinition(
                tool_key="test.tenant_lookup",
                provider="test",
                category="lookup",
                display_name="Tenant lookup",
                description="schema values owned partly by the adapter",
                input_schema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "tenant_id": {"type": "string"},
                        "limit": {"type": "integer"},
                    },
                    "required": ["query", "tenant_id", "limit"],
                    "additionalProperties": False,
                },
                adapter={
                    "auth_type": "none",
                    "default_arguments": {"limit": 5},
                    "fixed_arguments": {"tenant_id": "trusted-tenant"},
                },
            )
            catalog = ToolCatalog()
            catalog._definitions = {definition.tool_key: definition}
            adapter_runner = FakeAdapterRunner()
            executor = ToolExecutor(
                credential_resolver=FakeCredentialResolver(),
                catalog=catalog,
                adapter_runner=adapter_runner,
            )
            call = PlannedToolCall(
                call_id="call-tenant",
                tool_key=definition.tool_key,
                provider=definition.provider,
                category=definition.category,
                display_name=definition.display_name,
                confidence=1.0,
                reason="fixed argument boundary test",
                arguments={"query": "safe"},
            )

            result, events = await executor.execute(call)

            self.assertEqual(result.status, "success")
            self.assertEqual(adapter_runner.call.arguments, {"query": "safe", "limit": 5})
            self.assertNotIn("tenant_id", adapter_runner.call.arguments)
            start_event = [event for event in events if event.type == "tool_call_start"][0]
            self.assertNotIn("tenant_id", start_event.payload["arguments"])

        asyncio.run(run_test())

    def test_executor_rejects_non_object_arguments_before_adapter_side_effect(self) -> None:
        async def run_test() -> None:
            definition = ToolDefinition(
                tool_key="test.object",
                provider="test",
                category="lookup",
                display_name="Object lookup",
                description="object schema test",
                input_schema={"type": "object", "additionalProperties": False},
                adapter={"auth_type": "none"},
            )
            catalog = ToolCatalog()
            catalog._definitions = {definition.tool_key: definition}
            adapter_runner = FakeAdapterRunner()
            executor = ToolExecutor(
                credential_resolver=FakeCredentialResolver(),
                catalog=catalog,
                adapter_runner=adapter_runner,
            )
            call = PlannedToolCall(
                call_id="call-object",
                tool_key=definition.tool_key,
                provider=definition.provider,
                category=definition.category,
                display_name=definition.display_name,
                confidence=1.0,
                reason="object schema test",
                arguments=["not", "an", "object"],
            )

            result, events = await executor.execute(call)

            self.assertEqual(result.status, "skipped")
            self.assertIsNone(adapter_runner.call)
            self.assertEqual(
                [event for event in events if event.type == "tool_schema_validation"][-1].payload["status"],
                "failed",
            )

        asyncio.run(run_test())

    def test_executor_records_quality_gate_for_empty_success(self) -> None:
        async def run_test() -> None:
            definition = ToolDefinition(
                tool_key="test.empty",
                provider="test",
                category="test",
                display_name="Empty test",
                description="returns no evidence",
                adapter={"auth_type": "none"},
            )
            catalog = ToolCatalog()
            catalog._definitions = {definition.tool_key: definition}
            executor = ToolExecutor(
                credential_resolver=FakeCredentialResolver(),
                catalog=catalog,
                adapter_runner=EmptyAdapterRunner(),
            )
            call = PlannedToolCall(
                call_id="call-empty",
                tool_key=definition.tool_key,
                provider=definition.provider,
                category=definition.category,
                display_name=definition.display_name,
                confidence=1.0,
                reason="quality test",
            )

            result, events = await executor.execute(call)

            self.assertEqual(result.status, "success")
            self.assertEqual(result.quality_status, "invalid")
            self.assertIn("no_sources", result.quality_reasons)
            quality_event = [event for event in events if event.type == "tool_result_quality"]
            self.assertEqual(quality_event[0].payload["status"], "invalid")

        asyncio.run(run_test())

    def test_executor_only_accepts_empty_answer_from_trusted_local_adapter(self) -> None:
        async def run_test() -> None:
            remote_definition = ToolDefinition(
                tool_key="test.remote-empty",
                provider="test",
                category="test",
                display_name="Remote empty test",
                description="remote adapter must not self-certify an empty result",
                adapter_type="mcp_http",
                adapter={"auth_type": "none"},
                quality_contract={"allow_empty": True},
            )
            local_definition = ToolDefinition(
                tool_key="test.local-empty",
                provider="workspace",
                category="workspace_file",
                display_name="Local empty test",
                description="trusted local adapter can explicitly return no match",
                adapter_type="workspace_file",
                adapter={"auth_type": "none"},
                quality_contract={"allow_empty": True},
            )
            catalog = ToolCatalog()
            catalog._definitions = {
                remote_definition.tool_key: remote_definition,
                local_definition.tool_key: local_definition,
            }
            untrusted_executor = ToolExecutor(
                credential_resolver=FakeCredentialResolver(),
                catalog=catalog,
                adapter_runner=EmptyAnswerClaimingAdapterRunner(),
            )

            remote_result, _ = await untrusted_executor.execute(
                PlannedToolCall(
                    call_id="remote-empty",
                    tool_key=remote_definition.tool_key,
                    provider=remote_definition.provider,
                    category=remote_definition.category,
                    display_name=remote_definition.display_name,
                    confidence=1.0,
                    reason="remote adapter claim",
                )
            )
            local_result, local_events = await untrusted_executor.execute(
                PlannedToolCall(
                    call_id="local-empty",
                    tool_key=local_definition.tool_key,
                    provider=local_definition.provider,
                    category=local_definition.category,
                    display_name=local_definition.display_name,
                    confidence=1.0,
                    reason="local adapter claim",
                )
            )

            self.assertEqual(remote_result.result_semantics, "evidence")
            self.assertEqual(remote_result.quality_status, "invalid")
            self.assertIn("no_sources", remote_result.quality_reasons)
            # adapter_type 只是声明，外部 Runner 伪造本地类型也不能放宽质量门。
            self.assertEqual(local_result.result_semantics, "evidence")
            self.assertEqual(local_result.quality_status, "invalid")
            self.assertIn("no_sources", local_result.quality_reasons)

            trusted_executor = ToolExecutor(
                credential_resolver=FakeCredentialResolver(),
                catalog=catalog,
                adapter_runner=ToolAdapterRunner(
                    workspace_file_provider=InternalEmptyWorkspaceProvider(
                        db=None,
                        user_id=None,
                        project_id=None,
                    )
                ),
            )
            trusted_result, trusted_events = await trusted_executor.execute(
                PlannedToolCall(
                    call_id="trusted-local-empty",
                    tool_key=local_definition.tool_key,
                    provider=local_definition.provider,
                    category=local_definition.category,
                    display_name=local_definition.display_name,
                    confidence=1.0,
                    reason="trusted local adapter claim",
                )
            )
            self.assertEqual(trusted_result.result_semantics, "empty_answer")
            self.assertEqual(trusted_result.quality_status, "valid")
            self.assertEqual(
                [event for event in trusted_events if event.type == "tool_result_quality"][0]
                .payload["metadata"]["result_semantics"],
                "empty_answer",
            )

        asyncio.run(run_test())

    def test_executor_applies_declarative_profile_after_adapter_normalization(self) -> None:
        async def run_test() -> None:
            definition = ToolDefinition(
                tool_key="test.profile",
                provider="provider-a",
                category="web_search",
                display_name="Profile search",
                description="profile contract test",
                adapter_type="mcp_http",
                adapter={"auth_type": "none"},
                quality_contract={
                    "semantic_profile": "web_search",
                    "profile_mapping": {
                        "evidence_paths": ["/sources/*/metadata/raw/items/*/text"],
                        "identity_paths": ["/sources/*/metadata/raw/items/*/href"],
                        "collection_paths": ["/sources/*/metadata/raw/items"],
                        "item_evidence_paths": ["/text"],
                        "item_identity_paths": ["/href"],
                    },
                },
            )
            catalog = ToolCatalog()
            catalog._definitions = {definition.tool_key: definition}
            executor = ToolExecutor(
                credential_resolver=FakeCredentialResolver(),
                catalog=catalog,
                adapter_runner=ProfileAdapterRunner(),
            )

            result, events = await executor.execute(
                PlannedToolCall(
                    call_id="profile-call",
                    tool_key=definition.tool_key,
                    provider=definition.provider,
                    category=definition.category,
                    display_name=definition.display_name,
                    confidence=1.0,
                    reason="profile test",
                )
            )

            self.assertEqual(result.quality_status, "valid")
            self.assertEqual(result.quality_metadata["semantic_profile"], "web_search")
            quality_event = [event for event in events if event.type == "tool_result_quality"][0]
            self.assertEqual(quality_event.payload["result_semantics"], "evidence")

        asyncio.run(run_test())

    def test_executor_rejects_dynamic_mcp_evidence_without_reviewed_profile(self) -> None:
        async def run_test() -> None:
            definition = ToolDefinition(
                tool_key="mcp.dynamic.unreviewed",
                provider="dynamic",
                category="web_search",
                display_name="Dynamic MCP",
                description="unreviewed dynamic MCP result",
                adapter_type="mcp_http",
                adapter={"auth_type": "none"},
                source_type="mcp_server",
                quality_contract={"require_semantic_profile": True},
            )
            catalog = ToolCatalog()
            catalog._definitions = {definition.tool_key: definition}
            result, _ = await ToolExecutor(
                credential_resolver=FakeCredentialResolver(),
                catalog=catalog,
                adapter_runner=FakeAdapterRunner(),
            ).execute(
                PlannedToolCall(
                    call_id="dynamic-unreviewed",
                    tool_key=definition.tool_key,
                    provider=definition.provider,
                    category=definition.category,
                    display_name=definition.display_name,
                    confidence=1.0,
                    reason="验证未审核动态 MCP 失败关闭",
                )
            )

            self.assertEqual(result.status, "success")
            self.assertEqual(result.quality_status, "invalid")
            self.assertIn("semantic_profile_required", result.quality_reasons)

        asyncio.run(run_test())

    def test_executor_uses_trusted_mcp_normalized_arguments_for_route_quality(self) -> None:
        async def run_test() -> None:
            definition = ToolDefinition(
                tool_key="test.route",
                provider="amap",
                category="map_route",
                display_name="路线规划",
                description="地点名会在受控 MCP Adapter 中转换为坐标",
                adapter_type="mcp_http",
                input_schema={
                    "type": "object",
                    "properties": {
                        "origin": {"type": "string"},
                        "destination": {"type": "string"},
                    },
                    "required": ["origin", "destination"],
                    "additionalProperties": False,
                },
                adapter={"auth_type": "none"},
                quality_contract={
                    "semantic_profile": "route",
                    "profile_mapping": {
                        "evidence_paths": [
                            "/sources/*/metadata/raw/distance",
                            "/sources/*/metadata/raw/duration",
                        ],
                        "identity_paths": [
                            "/sources/*/metadata/raw/origin",
                            "/sources/*/metadata/raw/destination",
                        ],
                        "collection_paths": ["/sources"],
                        "item_evidence_paths": ["/metadata/raw/distance"],
                        "item_identity_paths": ["/metadata/raw/origin"],
                        "request_matches": [
                            {
                                "request_path": "/origin",
                                "result_paths": ["/sources/*/metadata/raw/origin"],
                                "normalizer": "coordinate",
                            },
                            {
                                "request_path": "/destination",
                                "result_paths": ["/sources/*/metadata/raw/destination"],
                                "normalizer": "coordinate",
                            },
                        ],
                    },
                },
            )
            catalog = ToolCatalog()
            catalog._definitions = {definition.tool_key: definition}
            call = PlannedToolCall(
                call_id="route-coordinate-normalization",
                tool_key=definition.tool_key,
                provider=definition.provider,
                category=definition.category,
                display_name=definition.display_name,
                confidence=1.0,
                reason="测试地点名到坐标的受控归一化",
                arguments={"origin": "广州塔", "destination": "华南理工大学"},
            )

            trusted_executor = ToolExecutor(
                credential_resolver=FakeCredentialResolver(),
                catalog=catalog,
                adapter_runner=CoordinateMcpAdapterRunner(),
            )
            trusted_result, _ = await trusted_executor.execute(call)
            self.assertEqual(trusted_result.quality_status, "valid")

            untrusted_executor = ToolExecutor(
                credential_resolver=FakeCredentialResolver(),
                catalog=catalog,
                adapter_runner=UntrustedCoordinateAdapterRunner(),
            )
            untrusted_result, _ = await untrusted_executor.execute(
                PlannedToolCall(
                    call_id="untrusted-route-coordinate-normalization",
                    tool_key=definition.tool_key,
                    provider=definition.provider,
                    category=definition.category,
                    display_name=definition.display_name,
                    confidence=1.0,
                    reason="测试不可信 Adapter 不能伪造质量上下文",
                    arguments={"origin": "广州塔", "destination": "华南理工大学"},
                )
            )
            self.assertEqual(untrusted_result.quality_status, "invalid")
            self.assertIn("request_result_mismatch:0", untrusted_result.quality_reasons)

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
