from __future__ import annotations

import asyncio
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from app.services.external_context_service import ExternalContextService
from app.services.tools.catalog import ToolCatalog
from app.services.tools.schemas import PlannedToolCall, ToolDefinition, ToolPlan


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


if __name__ == "__main__":
    unittest.main()
