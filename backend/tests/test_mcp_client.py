from __future__ import annotations

import asyncio
import unittest

import httpx

from app.services.tools.mcp_client import McpHttpClient
from app.services.tools.result_mappers import _extract_payload
from app.services.tools.result_mappers import map_mcp_result


class McpHttpClientTest(unittest.TestCase):
    def test_parse_json_response(self) -> None:
        parsed = McpHttpClient._parse_response_text('{"jsonrpc":"2.0","id":1,"result":{"ok":true}}')

        self.assertEqual(parsed["result"]["ok"], True)

    def test_parse_sse_response(self) -> None:
        parsed = McpHttpClient._parse_response_text(
            'event: message\n'
            'data: {"jsonrpc":"2.0","id":2,"result":{"tools":[{"name":"tavily_search"}]}}\n\n'
        )

        self.assertEqual(parsed["result"]["tools"][0]["name"], "tavily_search")

    def test_result_mapper_prefers_structured_content_over_compatibility_text(self) -> None:
        payload = _extract_payload(
            {
                "result": {
                    "structuredContent": {"temperature": 22.5, "city": "深圳"},
                    "content": [
                        {
                            "type": "text",
                            "text": '{"temperature": "compatibility-copy"}',
                        }
                    ],
                }
            }
        )

        self.assertEqual(payload, {"temperature": 22.5, "city": "深圳"})

    def test_empty_mcp_result_does_not_become_protocol_source(self) -> None:
        for raw in (
            {"jsonrpc": "2.0", "id": 1, "result": {}},
            {"jsonrpc": "2.0", "id": 1, "result": {"content": []}},
            {"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "image", "data": "ignored"}]}},
        ):
            with self.subTest(raw=raw):
                sources = map_mcp_result(
                    mapper="",
                    provider="test",
                    category="test",
                    display_name="Test",
                    query="query",
                    raw=raw,
                )
                self.assertEqual(sources, [])

    def test_post_jsonrpc_redacts_endpoint_secret_from_http_error(self) -> None:
        async def run_test() -> None:
            secret = "DUMMY_SECRET_123456"
            endpoint = f"https://example.test/mcp?api_key={secret}"

            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(401, request=request)

            client = McpHttpClient(endpoint=endpoint)
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
                with self.assertRaises(RuntimeError) as captured:
                    await client._post_jsonrpc(
                        http_client,
                        request_id=1,
                        method="initialize",
                        params={},
                    )

            message = str(captured.exception)
            self.assertEqual(message, "MCP HTTP 请求失败（status=401）")
            self.assertNotIn(secret, message)
            self.assertNotIn(endpoint, message)
            self.assertIsNone(captured.exception.__cause__)

        asyncio.run(run_test())

    def test_post_jsonrpc_does_not_expose_untrusted_remote_error_message(self) -> None:
        async def run_test() -> None:
            secret = "DUMMY_SECRET_REMOTE_ERROR"

            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json={
                        "jsonrpc": "2.0",
                        "id": 3,
                        "error": {"code": -32603, "message": f"failed with {secret}"},
                    },
                    request=request,
                )

            client = McpHttpClient(endpoint="https://example.test/mcp")
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
                with self.assertRaises(RuntimeError) as captured:
                    await client._post_jsonrpc(
                        http_client,
                        request_id=3,
                        method="tools/call",
                        params={"name": "weather", "arguments": {}},
                    )

            self.assertEqual(str(captured.exception), "MCP JSON-RPC 调用失败（code=-32603）。")
            self.assertNotIn(secret, str(captured.exception))

        asyncio.run(run_test())

    def test_post_jsonrpc_rejects_non_object_response(self) -> None:
        async def run_test() -> None:
            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    json=[{"jsonrpc": "2.0", "id": 3, "result": {}}],
                    request=request,
                )

            client = McpHttpClient(endpoint="https://example.test/mcp")
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
                with self.assertRaisesRegex(RuntimeError, "响应格式不合法"):
                    await client._post_jsonrpc(
                        http_client,
                        request_id=3,
                        method="tools/call",
                        params={"name": "weather", "arguments": {}},
                    )

        asyncio.run(run_test())

    def test_initialize_redacts_endpoint_secret_from_notification_error(self) -> None:
        async def run_test() -> None:
            secret = "DUMMY_SECRET_654321"
            endpoint = f"https://example.test/mcp?api_key={secret}"
            request_count = 0

            def handler(request: httpx.Request) -> httpx.Response:
                nonlocal request_count
                request_count += 1
                if request_count == 1:
                    return httpx.Response(
                        200,
                        headers={"content-type": "application/json"},
                        json={"jsonrpc": "2.0", "id": 1, "result": {}},
                        request=request,
                    )
                return httpx.Response(500, request=request)

            client = McpHttpClient(endpoint=endpoint)
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
                with self.assertRaises(RuntimeError) as captured:
                    await client._initialize(http_client)

            message = str(captured.exception)
            self.assertEqual(message, "MCP HTTP 请求失败（status=500）")
            self.assertNotIn(secret, message)
            self.assertNotIn(endpoint, message)
            self.assertIsNone(captured.exception.__cause__)

        asyncio.run(run_test())

    def test_initialize_propagates_server_session_id_to_followup_notification(self) -> None:
        async def run_test() -> None:
            requests: list[httpx.Request] = []

            def handler(request: httpx.Request) -> httpx.Response:
                requests.append(request)
                if len(requests) == 1:
                    return httpx.Response(
                        200,
                        headers={
                            "content-type": "application/json",
                            "Mcp-Session-Id": "session-123",
                        },
                        json={"jsonrpc": "2.0", "id": 1, "result": {}},
                        request=request,
                    )
                return httpx.Response(202, request=request)

            client = McpHttpClient(endpoint="https://example.test/mcp")
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
                await client._initialize(http_client)

            self.assertEqual(client.session_id, "session-123")
            self.assertEqual(requests[1].headers.get("Mcp-Session-Id"), "session-123")

        asyncio.run(run_test())

    def test_call_tool_treats_protocol_is_error_as_failure(self) -> None:
        async def run_test() -> None:
            client = McpHttpClient(endpoint="https://example.test/mcp")

            async def fake_initialize(_client: httpx.AsyncClient) -> None:
                return None

            async def fake_post_jsonrpc(*args, **kwargs) -> dict:
                return {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "result": {
                        "isError": True,
                        "content": [{"type": "text", "text": "remote details must not become facts"}],
                    },
                }

            client._initialize = fake_initialize  # type: ignore[method-assign]
            client._post_jsonrpc = fake_post_jsonrpc  # type: ignore[method-assign]

            with self.assertRaisesRegex(RuntimeError, "MCP 工具执行失败"):
                await client.call_tool(tool_name="weather", arguments={"city": "深圳"})

        asyncio.run(run_test())

    def test_call_tool_validates_structured_content_against_output_schema(self) -> None:
        async def run_test() -> None:
            client = McpHttpClient(endpoint="https://example.test/mcp")

            async def fake_initialize(_client: httpx.AsyncClient) -> None:
                return None

            async def fake_post_jsonrpc(*args, **kwargs) -> dict:
                return {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "result": {
                        "structuredContent": {"temperature": "not-a-number"},
                        "content": [{"type": "text", "text": "compatibility copy"}],
                    },
                }

            client._initialize = fake_initialize  # type: ignore[method-assign]
            client._post_jsonrpc = fake_post_jsonrpc  # type: ignore[method-assign]

            with self.assertRaisesRegex(RuntimeError, "不符合 outputSchema"):
                await client.call_tool(
                    tool_name="weather",
                    arguments={"city": "深圳"},
                    output_schema={
                        "type": "object",
                        "properties": {"temperature": {"type": "number"}},
                        "required": ["temperature"],
                    },
                )

        asyncio.run(run_test())

    def test_json_response_rejects_body_over_size_limit(self) -> None:
        async def run_test() -> None:
            body = b'{"jsonrpc":"2.0","id":1,"result":{"text":"' + (b"x" * 200) + b'"}}'

            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(
                    200,
                    headers={"content-type": "application/json"},
                    content=body,
                    request=request,
                )

            client = McpHttpClient(endpoint="https://example.test/mcp", max_response_bytes=64)
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
                with self.assertRaisesRegex(RuntimeError, "MCP 响应超过大小限制"):
                    await client._post_jsonrpc(
                        http_client,
                        request_id=1,
                        method="initialize",
                        params={},
                    )

        asyncio.run(run_test())

    def test_sse_response_rejects_event_over_size_limit(self) -> None:
        async def run_test() -> None:
            body = b'data: {"jsonrpc":"2.0","id":1,"result":{"text":"' + (b"x" * 200) + b'"}}\n\n'

            def handler(request: httpx.Request) -> httpx.Response:
                return httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    content=body,
                    request=request,
                )

            client = McpHttpClient(endpoint="https://example.test/mcp", max_response_bytes=64)
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
                with self.assertRaisesRegex(RuntimeError, "MCP 响应超过大小限制"):
                    await client._post_jsonrpc(
                        http_client,
                        request_id=1,
                        method="initialize",
                        params={},
                    )

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
