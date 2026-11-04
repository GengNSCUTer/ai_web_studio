"""新版逐请求、旧握手、有限兼容、分页和SSE最终响应的协议回归。"""

import asyncio
import base64
import json
import unittest
from unittest.mock import patch

import httpx

from app.services.tools.mcp_client import McpHttpClient
from app.services.tools.mcp_protocol import MODERN_VERSION, parameter_headers


def rpc(request, result=None, *, error=None, status=200, headers=None):
    body = json.loads(request.content)
    payload = {"jsonrpc": "2.0", "id": body["id"]}
    payload.update({"error": error} if error else {"result": result or {}})
    return httpx.Response(status, json=payload, headers=headers, request=request)


def discover(request, capabilities=None):
    return rpc(request, {"resultType": "complete", "supportedVersions": [MODERN_VERSION],
                         "capabilities": capabilities if capabilities is not None else {"tools": {}, "resources": {}}})


class McpCompatibilityTest(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, handler, action):
        original = httpx.AsyncClient
        def factory(**kwargs):
            # 协议格式测试使用MockTransport，不执行真实连接后端；连接安全有独立测试。
            kwargs.pop("transport", None)
            return original(transport=httpx.MockTransport(handler), **kwargs)
        with patch("app.services.tools.mcp_client.httpx.AsyncClient", side_effect=factory):
            return await action(McpHttpClient(endpoint="https://mcp.example.test/endpoint"))

    async def test_modern_has_metadata_every_request_no_initialize_or_session(self):
        methods = []
        def handler(request):
            data = json.loads(request.content); methods.append(data["method"])
            self.assertEqual(request.headers["MCP-Protocol-Version"], MODERN_VERSION)
            self.assertEqual(request.headers["Mcp-Method"], data["method"])
            self.assertEqual(data["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"], MODERN_VERSION)
            self.assertEqual(data["params"]["_meta"]["io.modelcontextprotocol/clientCapabilities"], {})
            self.assertNotIn("Mcp-Session-Id", request.headers)
            if data["method"] == "server/discover":
                response = discover(request); response.headers["Mcp-Session-Id"] = "must-ignore"
                return response
            return rpc(request, {"content": [{"type": "text", "text": "完成"}]})
        result = await self.invoke(handler, lambda client: client.call_tool(tool_name="天气", arguments={}, input_schema={}))
        self.assertEqual(methods, ["server/discover", "tools/call"])
        self.assertIn("content", result.raw["result"])

    async def test_legacy_fallback_handshake_and_optional_session(self):
        for session in (None, "session-for-legacy"):
            methods = []
            def handler(request):
                data = json.loads(request.content); methods.append(data["method"])
                if data["method"] == "server/discover":
                    return httpx.Response(400, content=b"legacy expects initialize", request=request)
                self.assertNotIn("_meta", data["params"])
                if data["method"] == "initialize":
                    return rpc(request, {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}},
                               headers={"Mcp-Session-Id": session} if session else {})
                self.assertEqual(request.headers.get("Mcp-Session-Id"), session)
                self.assertEqual(request.headers["MCP-Protocol-Version"], "2025-06-18")
                if data["method"] == "notifications/initialized":
                    return httpx.Response(202, request=request)
                return rpc(request, {"tools": [{"name": "demo", "inputSchema": {}}]})
            result = await self.invoke(handler, lambda client: client.list_tools())
            self.assertEqual([item.name for item in result], ["demo"])
            self.assertEqual(methods, ["server/discover", "initialize", "notifications/initialized", "tools/list"])

    async def test_auth_rate_limit_and_server_errors_do_not_trigger_fallback(self):
        for status in (401, 403, 429, 500, 503):
            requests = []
            def handler(request):
                requests.append(request)
                return httpx.Response(status, request=request)
            with self.assertRaises(RuntimeError):
                await self.invoke(handler, lambda client: client.list_tools())
            self.assertEqual(len(requests), 1)

    async def test_recognized_modern_errors_never_downgrade(self):
        for code in (-32020, -32021, -32022):
            methods = []
            def handler(request):
                methods.append(json.loads(request.content)["method"])
                return rpc(request, error={"code": code, "message": "secret must not propagate",
                    "data": {"supported": ["2099-01-01"]}}, status=400)
            with self.assertRaises(RuntimeError) as caught:
                await self.invoke(handler, lambda client: client.list_tools())
            self.assertNotIn("secret", str(caught.exception))
            self.assertEqual(methods, ["server/discover"])

    async def test_unsupported_version_can_choose_explicit_legacy_version(self):
        def handler(request):
            data = json.loads(request.content)
            if data["method"] == "server/discover":
                return rpc(request, error={"code": -32022, "data": {"supported": ["2025-11-25"]}}, status=400)
            if data["method"] == "initialize":
                self.assertEqual(data["params"]["protocolVersion"], "2025-11-25")
                return rpc(request, {"protocolVersion": "2025-11-25", "capabilities": {}})
            return httpx.Response(202, request=request)
        result = await self.invoke(handler, lambda client: client.discover())
        self.assertEqual(result["protocol_version"], "2025-11-25")

    async def test_missing_or_unsupported_initialize_version_is_rejected(self):
        for version in (None, MODERN_VERSION, "unknown"):
            methods = []
            def handler(request):
                data = json.loads(request.content); methods.append(data["method"])
                if data["method"] == "server/discover":
                    return httpx.Response(400, request=request)
                return rpc(request, {"protocolVersion": version})
            with self.assertRaisesRegex(RuntimeError, "未支持"):
                await self.invoke(handler, lambda client: client.list_tools())
            self.assertNotIn("notifications/initialized", methods)

    async def test_business_timeout_does_not_retry_or_initialize(self):
        methods = []
        def handler(request):
            method = json.loads(request.content)["method"]; methods.append(method)
            if method == "server/discover": return discover(request)
            raise httpx.ReadTimeout("hidden endpoint secret", request=request)
        with self.assertRaisesRegex(RuntimeError, "超时"):
            await self.invoke(handler, lambda client: client.call_tool(tool_name="demo", arguments={}, input_schema={}))
        self.assertEqual(methods, ["server/discover", "tools/call"])

    async def test_sse_skips_progress_and_joins_multiline_final_response(self):
        def handler(request):
            if json.loads(request.content)["method"] == "server/discover": return discover(request)
            identifier = json.loads(request.content)["id"]
            body = ('data: {"jsonrpc":"2.0","method":"notifications/progress","params":{}}\n\n'
                    f'data: {{"jsonrpc":"2.0","id":{identifier},\n'
                    'data: "result":{"content":[{"type":"text","text":"正文"}]}}\n\n')
            return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"}, request=request)
        result = await self.invoke(handler, lambda client: client.call_tool(tool_name="demo", arguments={}, input_schema={}))
        self.assertEqual(result.raw["result"]["content"][0]["text"], "正文")

    async def test_input_required_is_explicitly_not_a_success_or_auto_replayed(self):
        methods = []
        def handler(request):
            method = json.loads(request.content)["method"]; methods.append(method)
            if method == "server/discover": return discover(request)
            return rpc(request, {"resultType": "input_required", "inputRequests": []})
        with self.assertRaisesRegex(RuntimeError, "未自动继续"):
            await self.invoke(handler, lambda client: client.call_tool(tool_name="demo", arguments={}, input_schema={}))
        self.assertEqual(len(methods), 2)

    async def test_pagination_resources_and_read_uri_are_remote_rpc_only(self):
        def handler(request):
            data = json.loads(request.content)
            if data["method"] == "server/discover": return discover(request)
            if data["method"] == "resources/list":
                if "cursor" not in data["params"]:
                    return rpc(request, {"resources": [{"uri": "file:///资料/a.md"}], "nextCursor": "next"})
                return rpc(request, {"resources": [{"uri": "resource://b"}]})
            self.assertEqual(data["params"]["uri"], "file:///资料/a.md")
            self.assertTrue(request.headers["Mcp-Name"].startswith("=?base64?"))
            return rpc(request, {"contents": [{"uri": "file:///资料/a.md", "text": "正文"}]})
        listed = await self.invoke(handler, lambda client: client.list_resources())
        self.assertEqual(len(listed), 2)
        result = await self.invoke(handler, lambda client: client.read_resource("file:///资料/a.md"))
        self.assertEqual(result["contents"][0]["text"], "正文")

    async def test_repeated_cursor_is_rejected(self):
        def handler(request):
            if json.loads(request.content)["method"] == "server/discover": return discover(request)
            return rpc(request, {"resources": [], "nextCursor": "same"})
        with self.assertRaisesRegex(RuntimeError, "重复"):
            await self.invoke(handler, lambda client: client.list_resources())

    async def test_absent_resource_capability_is_not_silently_invented(self):
        methods = []
        def handler(request):
            methods.append(json.loads(request.content)["method"])
            return discover(request, {"tools": {}})
        self.assertEqual(await self.invoke(handler, lambda client: client.list_resources()), [])
        self.assertEqual(methods, ["server/discover"])

    async def test_resource_only_server_does_not_receive_a_tool_call(self):
        methods = []
        def handler(request):
            methods.append(json.loads(request.content)["method"])
            return discover(request, {"resources": {}})
        with self.assertRaisesRegex(RuntimeError, "未声明Tools"):
            await self.invoke(handler, lambda client: client.call_tool(tool_name="demo", arguments={}, input_schema={}))
        self.assertEqual(methods, ["server/discover"])

    async def test_contradictory_supported_version_does_not_silently_downgrade(self):
        methods = []
        def handler(request):
            methods.append(json.loads(request.content)["method"])
            return rpc(request, error={"code": -32022, "data": {"supported": [MODERN_VERSION, "2025-03-26"]}}, status=400)
        with self.assertRaisesRegex(RuntimeError, "不一致"):
            await self.invoke(handler, lambda client: client.list_tools())
        self.assertEqual(methods, ["server/discover"])

    async def test_parameter_header_types_encoding_and_invalid_schema(self):
        schema = {"type": "object", "properties": {"region": {"type": "string", "x-mcp-header": "Region"},
                  "options": {"type": "object", "properties": {"enabled": {"type": "boolean", "x-mcp-header": "Enabled"}}}}}
        actual = parameter_headers(schema, {"region": "广东\n", "options": {"enabled": True}})
        self.assertEqual(actual["Mcp-Param-Enabled"], "true")
        encoded = actual["Mcp-Param-Region"][9:-2]
        self.assertEqual(base64.b64decode(encoded).decode(), "广东\n")
        for invalid in ({"type": "object", "items": {"type": "string", "x-mcp-header": "X"}},
                        {"properties": {"x": {"type": ["string", "null"], "x-mcp-header": "X"}}}):
            with self.assertRaises(RuntimeError): parameter_headers(invalid, {})
