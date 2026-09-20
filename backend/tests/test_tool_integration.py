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
    """固定 Planner，用于验证外层 Chat 如何收口并发与严格依赖。"""

    def __init__(self, *, dependency: bool = False) -> None:
        self.dependency = dependency

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
            can_parallel=not self.dependency,
        )
        if self.dependency:
            route = PlannedToolCall(
                call_id="route",
                tool_key="amap.maps.direction.driving",
                provider="amap",
                category="map_route",
                display_name="高德路线",
                confidence=1.0,
                reason="depends on weather",
                arguments={"origin": "深圳", "destination": "广州"},
                depends_on=["weather"],
                can_parallel=False,
            )
            calls = [weather, route]
        else:
            calls = [
                weather,
                PlannedToolCall(
                    call_id="search",
                    tool_key="web.tavily.search",
                    provider="tavily",
                    category="web_search",
                    display_name="Tavily 搜索",
                    confidence=1.0,
                    reason="independent branch",
                    arguments={"query": "深圳天气", "max_results": 2},
                    can_parallel=True,
                ),
            ]
        return ToolPlan(
            plan_id="stage-3-4-plan",
            router="fixed_integration_planner",
            external_context_allowed=True,
            should_use_tools=True,
            need_more_rounds=False,
            calls=calls,
        )


class MultiToolExecutor:
    """不联网的执行替身，只返回脱敏结果以验证外层状态传播。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(self, call: PlannedToolCall):
        self.calls.append(call.call_id)
        source = ExternalSource(
            source_type=call.category,
            provider=call.provider,
            title=f"{call.display_name}测试结果",
            display_text=f"{call.display_name}测试证据",
        )
        invalid = call.call_id == "weather"
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
        executor = MultiToolExecutor()
        workflow = ToolWorkflowService(executor=executor, registry=catalog)
        result = asyncio.run(
            ExternalContextService(
                registry=catalog,
                executor=executor,
                workflow=workflow,
                planner=MultiToolPlanner(),
            ).build_context(query="验证独立分支", enabled=True, max_chars=2000)
        )

        self.assertEqual(result.diagnostics["external_tool_workflow_aggregate_status"], "partial")
        self.assertGreater(len(result.sources), 0)
        self.assertTrue(all(source.source_type == "web_search" for source in result.sources))
        self.assertNotIn("weather", [source.source_type for source in result.sources])
        self.assertNotIn("route", executor.calls)

    def test_external_context_blocks_strict_dependent_tool_after_invalid_upstream(self) -> None:
        catalog = ToolCatalog()
        executor = MultiToolExecutor()
        workflow = ToolWorkflowService(executor=executor, registry=catalog)
        result = asyncio.run(
            ExternalContextService(
                registry=catalog,
                executor=executor,
                workflow=workflow,
                planner=MultiToolPlanner(dependency=True),
            ).build_context(query="验证严格依赖", enabled=True, max_chars=2000)
        )

        self.assertEqual(result.diagnostics["external_tool_workflow_aggregate_status"], "blocked")
        self.assertEqual(result.diagnostics["external_tool_next_action"], "clarify")
        self.assertEqual(executor.calls.count("route"), 0)
        self.assertIn("weather", executor.calls)


if __name__ == "__main__":
    unittest.main()
