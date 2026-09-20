from __future__ import annotations

import asyncio
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from app.services.external_context_service import ExternalContextService
from app.services.tools.catalog import ToolCatalog
from app.services.tools.schemas import (
    ExternalSource,
    PlannedToolCall,
    ToolCallResult,
    ToolDefinition,
    ToolPlan,
    ToolTraceEvent,
)
from app.services.tools.workflow import ToolWorkflowService


class FixedPlanner:
    """Keep this integration test focused on execution after planning."""

    def __init__(self, definition: ToolDefinition) -> None:
        self.definition = definition

    async def plan(self, **_: Any) -> ToolPlan:
        return ToolPlan(
            plan_id="plan-local-mcp",
            router="fixed_test_planner",
            external_context_allowed=True,
            should_use_tools=True,
            calls=[
                PlannedToolCall(
                    call_id="call-local-weather",
                    tool_key=self.definition.tool_key,
                    provider=self.definition.provider,
                    category=self.definition.category,
                    display_name=self.definition.display_name,
                    confidence=1.0,
                    reason="exercise the complete local MCP execution chain",
                    arguments={"city": "深圳"},
                )
            ],
        )


class RecordingMcpHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    session_id = "session-123"
    requests: list[dict[str, Any]] = []
    requests_lock = threading.Lock()

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        body_length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(body_length) or b"{}")
        with self.requests_lock:
            self.requests.append(
                {
                    "method": payload.get("method"),
                    "params": payload.get("params"),
                    "session_id": self.headers.get("Mcp-Session-Id"),
                }
            )

        if payload.get("method") == "notifications/initialized":
            self.send_response(202)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        if payload.get("method") == "initialize":
            self._send_json(
                {
                    "jsonrpc": "2.0",
                    "id": payload.get("id"),
                    "result": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "local-test-mcp", "version": "1.0"},
                    },
                },
                extra_headers={"Mcp-Session-Id": self.session_id},
            )
            return

        if payload.get("method") == "tools/call":
            self._send_json(
                {
                    "jsonrpc": "2.0",
                    "id": payload.get("id"),
                    "result": {
                        "structuredContent": {"city": "深圳", "temperature": 26},
                        "content": [
                            {
                                "type": "text",
                                "text": '{"city":"深圳","temperature":26}',
                            }
                        ],
                    },
                }
            )
            return

        self._send_json(
            {
                "jsonrpc": "2.0",
                "id": payload.get("id"),
                "error": {"code": -32601, "message": "method not found"},
            },
            status=404,
        )

    def _send_json(
        self,
        payload: dict[str, Any],
        *,
        status: int = 200,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, _format: str, *_args: Any) -> None:
        return


class MultiToolPlanner:
    """固定 Planner，用于验证多工具组合在 Chat 外层的确定收口。"""

    def __init__(self, *, scenario: str = "partial") -> None:
        self.scenario = scenario

    async def plan(self, **_: Any) -> ToolPlan:
        weather = PlannedToolCall(
            call_id="weather",
            tool_key="amap.maps.weather",
            provider="amap",
            category="weather",
            display_name="高德天气",
            confidence=1.0,
            reason="stage 3.4 integration",
            arguments={"city": "不存在的测试城市"},
            can_parallel=self.scenario != "waiting_approval",
        )
        search = PlannedToolCall(
            call_id="search",
            tool_key="web.tavily.search",
            provider="tavily",
            category="web_search",
            display_name="Tavily 搜索",
            confidence=1.0,
            reason="independent branch",
            arguments={"query": "深圳天气", "max_results": 2},
            can_parallel=True,
        )
        if self.scenario in {"dependency_invalid", "waiting_approval"}:
            route = PlannedToolCall(
                call_id="route",
                tool_key="amap.maps.direction.driving",
                provider="amap",
                category="map_route",
                display_name="高德路线",
                confidence=1.0,
                reason="depends on the first tool result",
                arguments={"origin": "深圳", "destination": "广州"},
                depends_on=["weather"],
                can_parallel=False,
            )
            calls = [weather, route]
        else:
            calls = [weather, search]
        return ToolPlan(
            plan_id="stage-3-4-plan",
            router="fixed_integration_planner",
            external_context_allowed=True,
            should_use_tools=True,
            need_more_rounds=False,
            calls=calls,
        )


class MultiToolExecutor:
    """不联网的执行替身，覆盖阶段 3.4 的组合状态传播。"""

    def __init__(self, *, scenario: str = "partial") -> None:
        self.scenario = scenario
        self.calls: list[str] = []
        self.active_calls = 0
        self.max_active_calls = 0

    async def execute(self, call: PlannedToolCall):
        self.calls.append(call.call_id)
        self.active_calls += 1
        self.max_active_calls = max(self.max_active_calls, self.active_calls)
        # 让并发全成功用例能够验证实际并发，而不只检查 Plan 中的标志位。
        if self.scenario == "all_success":
            await asyncio.sleep(0.01)
        try:
            if self.scenario == "all_failed":
                return (
                    ToolCallResult(
                        call=call,
                        status="failed",
                        sources=[],
                        elapsed_ms=1,
                        error_message="模拟工具不可用",
                    ),
                    [],
                )

            if self.scenario == "waiting_approval" and call.call_id == "weather":
                return (
                    ToolCallResult(
                        call=call,
                        status="confirmation_required",
                        sources=[
                            ExternalSource(
                                source_type="workspace_file_edit_preview",
                                provider="workspace",
                                title="待确认的修改提案",
                                display_text="这是尚未写入的受控修改预览。",
                            )
                        ],
                        elapsed_ms=1,
                        error_message="等待用户确认",
                    ),
                    [
                        ToolTraceEvent(
                            type="tool_confirmation_required",
                            payload={"call_id": call.call_id, "status": "waiting_approval"},
                        )
                    ],
                )

            source = ExternalSource(
                source_type=call.category,
                provider=call.provider,
                title=f"{call.display_name}测试结果",
                display_text=f"{call.display_name}测试证据",
            )
            invalid = self.scenario in {"partial", "dependency_invalid"} and call.call_id == "weather"
            result = ToolCallResult(
                call=call,
                status="success",
                sources=[source],
                elapsed_ms=1,
                quality_status="invalid" if invalid else "valid",
                quality_reasons=["test_quality_failure"] if invalid else [],
            )
            return result, [
                ToolTraceEvent(
                    type="tool_call_end",
                    payload={"call_id": call.call_id, "tool_key": call.tool_key, "status": "success"},
                )
            ]
        finally:
            self.active_calls -= 1


class ToolIntegrationTest(unittest.TestCase):
    def test_external_context_runs_complete_no_auth_mcp_success_chain(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), RecordingMcpHandler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        with RecordingMcpHandler.requests_lock:
            RecordingMcpHandler.requests = []
        server_thread.start()

        try:
            port = server.server_address[1]
            definition = ToolDefinition(
                tool_key="local.weather.lookup",
                provider="local_test_mcp",
                category="weather",
                display_name="本地天气",
                description="Look up local test weather.",
                input_schema={
                    "type": "object",
                    "properties": {"city": {"type": "string"}},
                    "required": ["city"],
                    "additionalProperties": False,
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "city": {"type": "string"},
                        "temperature": {"type": "number"},
                    },
                    "required": ["city", "temperature"],
                    "additionalProperties": False,
                },
                adapter_type="mcp_http",
                adapter={
                    "endpoint_template": f"http://127.0.0.1:{port}/mcp",
                    "mcp_tool_name": "weather_lookup",
                    "auth_type": "none",
                },
                # A project-owned local manifest endpoint does not pass through
                # the dynamic user-added MCP SSRF gate in this component test.
                source_type="local_manifest",
                risk_level="low",
                read_only=True,
            )
            registry = ToolCatalog()
            registry._definitions = {definition.tool_key: definition}
            service = ExternalContextService(
                registry=registry,
                planner=FixedPlanner(definition),
            )

            result = asyncio.run(
                service.build_context(
                    query="深圳现在多少度？",
                    enabled=True,
                    max_chars=4000,
                )
            )

            self.assertEqual(result.diagnostics["external_tool_called"], "weather")
            self.assertEqual(result.diagnostics["external_sources_total"], 1)
            self.assertEqual(len(result.sources), 1)
            self.assertIn("深圳", result.sources[0].display_text)
            self.assertIn('"temperature": 26', result.sources[0].display_text)
            self.assertIn("本地天气结果 1", result.context_text or "")
            self.assertIn('"temperature": 26', result.context_text or "")

            event_types = [event.type for event in result.tool_events]
            self.assertIn("tool_policy_check", event_types)
            self.assertIn("tool_call_start", event_types)
            self.assertIn("tool_call_end", event_types)
            self.assertIn("tool_workflow_end", event_types)
            policy = [
                event
                for event in result.tool_events
                if event.type == "tool_policy_check" and event.payload.get("status") == "passed"
            ][0]
            self.assertEqual(policy.payload["credential_source"], "not_required")

            with RecordingMcpHandler.requests_lock:
                requests = list(RecordingMcpHandler.requests)
            self.assertEqual(
                [request["method"] for request in requests],
                ["initialize", "notifications/initialized", "tools/call"],
            )
            self.assertIsNone(requests[0]["session_id"])
            self.assertEqual(requests[1]["session_id"], RecordingMcpHandler.session_id)
            self.assertEqual(requests[2]["session_id"], RecordingMcpHandler.session_id)
            self.assertEqual(requests[2]["params"]["name"], "weather_lookup")
            self.assertEqual(requests[2]["params"]["arguments"], {"city": "深圳"})
        finally:
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=3)

    def test_external_context_keeps_independent_success_when_one_branch_is_invalid(self) -> None:
        catalog = ToolCatalog()
        executor = MultiToolExecutor(scenario="partial")
        workflow = ToolWorkflowService(executor=executor, registry=catalog)
        result = asyncio.run(
            ExternalContextService(
                registry=catalog,
                executor=executor,
                workflow=workflow,
                planner=MultiToolPlanner(scenario="partial"),
            ).build_context(query="验证独立分支", enabled=True, max_chars=2000)
        )

        self.assertEqual(result.diagnostics["external_tool_workflow_aggregate_status"], "partial")
        self.assertGreater(len(result.sources), 0)
        self.assertTrue(all(source.source_type == "web_search" for source in result.sources))
        self.assertNotIn("weather", [source.source_type for source in result.sources])
        self.assertNotIn("route", executor.calls)

    def test_external_context_blocks_strict_dependent_tool_after_invalid_upstream(self) -> None:
        catalog = ToolCatalog()
        executor = MultiToolExecutor(scenario="dependency_invalid")
        workflow = ToolWorkflowService(executor=executor, registry=catalog)
        result = asyncio.run(
            ExternalContextService(
                registry=catalog,
                executor=executor,
                workflow=workflow,
                planner=MultiToolPlanner(scenario="dependency_invalid"),
            ).build_context(query="验证严格依赖", enabled=True, max_chars=2000)
        )

        self.assertEqual(result.diagnostics["external_tool_workflow_aggregate_status"], "blocked")
        self.assertEqual(result.diagnostics["external_tool_next_action"], "clarify")
        self.assertEqual(executor.calls.count("route"), 0)
        self.assertIn("weather", executor.calls)

    def test_external_context_keeps_all_parallel_successful_results(self) -> None:
        """两个独立工具均成功时，Chat 应保留全部有效证据并真正并发执行。"""

        catalog = ToolCatalog()
        executor = MultiToolExecutor(scenario="all_success")
        workflow = ToolWorkflowService(executor=executor, registry=catalog)
        result = asyncio.run(
            ExternalContextService(
                registry=catalog,
                executor=executor,
                workflow=workflow,
                planner=MultiToolPlanner(scenario="all_success"),
            ).build_context(query="验证并发全成功", enabled=True, max_chars=2000)
        )

        self.assertEqual(result.diagnostics["external_tool_workflow_aggregate_status"], "succeeded")
        self.assertEqual(result.diagnostics["external_tool_next_action"], "stop")
        self.assertEqual({source.source_type for source in result.sources}, {"weather", "web_search"})
        self.assertEqual(set(executor.calls), {"weather", "search"})
        self.assertGreaterEqual(executor.max_active_calls, 2)

    def test_external_context_stops_without_evidence_after_all_independent_branches_fail(self) -> None:
        """全路失败可受限重规划，但耗尽轮次后不得伪造部分回答。"""

        catalog = ToolCatalog()
        executor = MultiToolExecutor(scenario="all_failed")
        workflow = ToolWorkflowService(executor=executor, registry=catalog)
        result = asyncio.run(
            ExternalContextService(
                registry=catalog,
                executor=executor,
                workflow=workflow,
                planner=MultiToolPlanner(scenario="all_failed"),
            ).build_context(query="验证全路失败", enabled=True, max_chars=2000)
        )

        self.assertEqual(result.diagnostics["external_tool_workflow_aggregate_status"], "failed")
        self.assertEqual(result.diagnostics["external_tool_next_action"], "stop")
        self.assertEqual(result.diagnostics["external_agent_terminal_reason"], "max_rounds_reached")
        self.assertEqual(result.sources, [])
        self.assertEqual(set(executor.calls), {"weather", "search"})
        self.assertEqual(
            result.diagnostics["external_tool_run_budget"]["planning_rounds_used"],
            result.diagnostics["external_tool_run_budget"]["max_planning_rounds"],
        )

    def test_external_context_waits_for_confirmation_and_blocks_dependent_tool(self) -> None:
        """确认前只展示草案，并阻断依赖步骤与后续自动执行。"""

        catalog = ToolCatalog()
        executor = MultiToolExecutor(scenario="waiting_approval")
        workflow = ToolWorkflowService(executor=executor, registry=catalog)
        result = asyncio.run(
            ExternalContextService(
                registry=catalog,
                executor=executor,
                workflow=workflow,
                planner=MultiToolPlanner(scenario="waiting_approval"),
            ).build_context(query="验证等待确认", enabled=True, max_chars=2000)
        )

        self.assertEqual(result.diagnostics["external_tool_workflow_aggregate_status"], "waiting_approval")
        self.assertEqual(result.diagnostics["external_tool_next_action"], "clarify")
        self.assertEqual(result.diagnostics["external_agent_terminal_reason"], "waiting_approval")
        self.assertEqual(executor.calls, ["weather"])
        self.assertNotIn("route", executor.calls)
        self.assertEqual(len(result.sources), 1)


if __name__ == "__main__":
    unittest.main()
