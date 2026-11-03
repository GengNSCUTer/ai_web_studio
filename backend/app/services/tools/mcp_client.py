from __future__ import annotations

import json
import asyncio
from dataclasses import dataclass
from typing import Any

import httpx
from jsonschema import Draft202012Validator, SchemaError, ValidationError

from app.core.config import settings
from app.services.tools.mcp_protocol import MODERN_VERSION, LEGACY_VERSIONS, CLIENT_INFO, header_value, parameter_headers


@dataclass
class McpTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    output_schema: dict[str, Any]
    raw: dict[str, Any]


@dataclass
class McpCallResponse:
    raw: dict[str, Any]


class McpProtocolError(RuntimeError):
    """仅保留可用于版本兼容判断的安全字段，不传播远端错误正文。"""

    def __init__(self, *, code=None, status=None, supported=()):
        self.code, self.status = code, status
        self.supported = tuple(value for value in supported if value in (MODERN_VERSION, *LEGACY_VERSIONS))
        suffix = f"（code={code}）" if isinstance(code, int) else ""
        super().__init__(f"MCP JSON-RPC 调用失败{suffix}。" if code is not None
                         else f"MCP HTTP 请求失败（status={status}）")


class McpHttpClient:
    """两代Streamable HTTP：新版逐请求元数据，旧版初始化和可选Session。"""

    protocol_version = "2025-03-26"

    def __init__(
        self,
        *,
        endpoint: str,
        timeout_seconds: int | None = None,
        extra_headers: dict[str, str] | None = None,
        max_response_bytes: int | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds or settings.external_tool_timeout_seconds
        self.extra_headers = extra_headers or {}
        self.max_response_bytes = max_response_bytes or settings.mcp_max_response_bytes
        self.session_id: str | None = None
        self.mode = "legacy"
        self.capabilities: dict[str, Any] = {}
        self._tool_schemas: dict[str, dict] = {}

    async def _connect(self, client: httpx.AsyncClient) -> None:
        # 只用无副作用发现请求判断协议，绝不因业务调用超时而换协议重放。
        self.session_id, self.mode, self.protocol_version = None, "modern", MODERN_VERSION
        try:
            data = await self._post_jsonrpc(client, request_id=1, method="server/discover", params={})
        except McpProtocolError as exc:
            if exc.code == -32022:
                if MODERN_VERSION in exc.supported:
                    raise RuntimeError("MCP服务的版本声明与拒绝响应不一致。") from None
                legacy = next((v for v in LEGACY_VERSIONS if v in exc.supported), None)
                if legacy is None:
                    raise RuntimeError("MCP服务与客户端没有共同支持的协议版本。") from None
                self.protocol_version = legacy
            elif exc.code in {-32020, -32021} or exc.status in {401, 403, 429}:
                raise
            elif exc.status not in {400, 404, 405} and exc.code != -32601:
                raise
            else:
                self.protocol_version = "2025-03-26"
            self.mode = "legacy"
            await self._initialize(client)
            return
        result = data["result"]
        versions = result.get("supportedVersions")
        if not isinstance(versions, list) or MODERN_VERSION not in versions:
            raise RuntimeError("MCP新版发现响应缺少受支持版本。")
        self.capabilities = result.get("capabilities") if isinstance(result.get("capabilities"), dict) else {}

    async def discover(self) -> dict:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            await self._connect(client)
        return {"protocol_version": self.protocol_version, "protocol_mode": self.mode,
                "capabilities": self.capabilities}

    async def list_resources(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            await self._connect(client)
            if "resources" not in self.capabilities:
                return []
            return await self._paged(client, "resources/list", "resources")

    async def list_resource_templates(self) -> list[dict]:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            await self._connect(client)
            if "resources" not in self.capabilities:
                return []
            return await self._paged(client, "resources/templates/list", "resourceTemplates")

    async def read_resource(self, uri: str) -> dict:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            await self._connect(client)
            if "resources" not in self.capabilities:
                raise RuntimeError("MCP服务未声明Resources能力。")
            data = await self._post_jsonrpc(client, request_id=4, method="resources/read", params={"uri": uri})
        contents = data["result"].get("contents")
        if not isinstance(contents, list) or not contents or any(not isinstance(item, dict) for item in contents):
            raise RuntimeError("MCP资源响应缺少有效内容。")
        return data["result"]

    async def _paged(self, client, method, field):
        values, cursor, seen = [], None, set()
        for page in range(20):
            data = await self._post_jsonrpc(client, request_id=10 + page, method=method,
                                           params={"cursor": cursor} if cursor else {})
            result = data["result"]
            items = result.get(field, [])
            if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
                raise RuntimeError("MCP列表响应格式无效。")
            values.extend(items)
            if len(values) > 2000:
                raise RuntimeError("MCP列表超过数量限制。")
            cursor = result.get("nextCursor")
            if not cursor:
                return values
            if not isinstance(cursor, str) or cursor in seen:
                raise RuntimeError("MCP列表分页游标无效或重复。")
            seen.add(cursor)
        raise RuntimeError("MCP列表超过分页限制。")

    async def list_tools(self) -> list[McpTool]:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            await self._connect(client)
            if "tools" not in self.capabilities:
                return []
            tools = await self._paged(client, "tools/list", "tools")
        if self.mode == "modern":
            valid_tools = []
            for tool in tools:
                try:
                    parameter_headers(tool.get("inputSchema") or {}, {})
                except RuntimeError:
                    continue
                valid_tools.append(tool)
            tools = valid_tools
        self._tool_schemas = {tool["name"]: tool.get("inputSchema") or {} for tool in tools if tool.get("name")}
        return [
            McpTool(
                name=str(tool.get("name") or ""),
                description=str(tool.get("description") or ""),
                input_schema=dict(tool.get("inputSchema") or {}),
                output_schema=dict(tool.get("outputSchema") or {}),
                raw=tool,
            )
            for tool in tools
            if tool.get("name")
        ]

    async def call_tool(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        output_schema: dict[str, Any] | None = None,
        input_schema: dict[str, Any] | None = None,
    ) -> McpCallResponse:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            await self._connect(client)
            if self.mode == "modern" and "tools" not in self.capabilities:
                raise RuntimeError("MCP服务未声明Tools能力。")
            if self.mode == "modern" and input_schema is None:
                tools = await self._paged(client, "tools/list", "tools")
                input_schema = next((tool.get("inputSchema") or {} for tool in tools if tool.get("name") == tool_name), None)
                if input_schema is None:
                    raise RuntimeError("MCP工具未出现在服务发现列表中。")
            data = await self._post_jsonrpc(
                client,
                request_id=3,
                method="tools/call",
                params={"name": tool_name, "arguments": arguments},
                input_schema=input_schema,
            )
        result = data.get("result")
        # MCP 的工具业务错误通常仍是成功的 JSON-RPC 响应，只在 result.isError
        # 标记失败。若不识别，失败文本会被当成正常外部事实喂给最终模型。
        if isinstance(result, dict) and result.get("isError") is True:
            raise RuntimeError("MCP 工具执行失败。")
        if output_schema:
            structured = result.get("structuredContent") if isinstance(result, dict) else None
            if structured is None:
                raise RuntimeError("MCP 工具声明了 outputSchema，但响应缺少 structuredContent。")
            try:
                Draft202012Validator.check_schema(output_schema)
                Draft202012Validator(output_schema).validate(structured)
            except (SchemaError, ValidationError):
                # 不把远端结构化结果或 schema 细节写入 Trace，避免泄漏或错误放大。
                raise RuntimeError("MCP structuredContent 不符合 outputSchema。") from None
        return McpCallResponse(raw=data)

    async def _initialize(self, client: httpx.AsyncClient) -> None:
        self.mode = "legacy"
        data = await self._post_jsonrpc(
            client,
            request_id=1,
            method="initialize",
            params={
                "protocolVersion": self.protocol_version,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
            include_protocol_header=False,
        )
        result = data["result"]
        version = result.get("protocolVersion")
        if version not in LEGACY_VERSIONS:
            raise RuntimeError("MCP初始化返回了未支持的旧版协议。")
        self.protocol_version = version
        self.capabilities = result.get("capabilities") if isinstance(result.get("capabilities"), dict) else {}
        try:
            # notification 的响应 body 没有业务用途，用 stream 避免恶意 Server 返回超大 body 时被 client.post 全量读入内存。
            async with client.stream(
                "POST",
                self.endpoint,
                headers=self._headers(include_protocol_header=True),
                json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            ) as response:
                response.raise_for_status()
        except httpx.HTTPError as exc:
            # endpoint 可能把 API Key 放在 query string；绝不能把 httpx 的完整 URL 异常继续向上抛。
            raise self._sanitize_http_error(exc) from None

    async def _post_jsonrpc(
        self, client: httpx.AsyncClient, **kwargs,
    ) -> dict[str, Any]:
        try:
            return await asyncio.wait_for(self._send_jsonrpc(client, **kwargs), self.timeout_seconds)
        except asyncio.TimeoutError:
            raise RuntimeError("MCP 请求超时") from None

    async def _send_jsonrpc(
        self,
        client: httpx.AsyncClient,
        *,
        request_id: int,
        method: str,
        params: dict[str, Any],
        include_protocol_header: bool = True,
        input_schema: dict | None = None,
    ) -> dict[str, Any]:
        params = dict(params)
        modern = self.mode == "modern"
        if modern:
            params["_meta"] = {"io.modelcontextprotocol/protocolVersion": self.protocol_version,
                               "io.modelcontextprotocol/clientInfo": CLIENT_INFO,
                               "io.modelcontextprotocol/clientCapabilities": {}}
        headers = self._headers(include_protocol_header=include_protocol_header)
        if modern:
            headers["Mcp-Method"] = method
            name = params.get("name") if method == "tools/call" else params.get("uri") if method == "resources/read" else None
            if name is not None:
                headers["Mcp-Name"] = header_value(str(name))
            if input_schema is not None:
                headers.update(parameter_headers(input_schema, params.get("arguments") or {}))
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        try:
            async with client.stream(
                "POST",
                self.endpoint,
                headers=headers,
                json=payload,
            ) as response:
                if 300 <= response.status_code < 400 or response.status_code in {401, 403, 429} or response.status_code >= 500:
                    response.raise_for_status()
                try:
                    data = await self._parse_streaming_response(response, request_id=request_id)
                except (ValueError, RuntimeError) as exc:
                    if response.status_code in {400, 404, 405}:
                        raise McpProtocolError(status=response.status_code) from None
                    if isinstance(exc, ValueError):
                        raise RuntimeError("MCP JSON-RPC 响应格式不合法。") from None
                    raise
                if not modern:
                    self._capture_session_id(response)
                status_code = response.status_code
        except httpx.HTTPError as exc:
            # httpx 的 HTTPStatusError 会包含完整请求 URL；这里在进入 Tool Trace 前去掉 endpoint/credential。
            raise self._sanitize_http_error(exc) from None
        if not isinstance(data, dict):
            raise RuntimeError("MCP JSON-RPC 响应格式不合法。")
        if "error" in data:
            if data.get("id") is not None and data.get("id") != request_id:
                raise RuntimeError("MCP JSON-RPC响应编号不匹配。")
            error = data["error"]
            # 远端 message 是不可信文本，可能回显凭证/参数；Trace 只保留协议错误码。
            code = error.get("code") if isinstance(error, dict) else None
            supported = (error.get("data") or {}).get("supported", []) if isinstance(error, dict) and isinstance(error.get("data"), dict) else []
            raise McpProtocolError(code=code, status=status_code, supported=supported if isinstance(supported, list) else [])
        if status_code >= 400:
            raise McpProtocolError(status=status_code)
        if data.get("jsonrpc") != "2.0" or data.get("id") != request_id:
            raise RuntimeError("MCP JSON-RPC响应编号不匹配。")
        result = data.get("result")
        if not isinstance(result, dict):
            raise RuntimeError("MCP响应缺少结果对象。")
        if result.get("resultType", "complete") != "complete":
            raise RuntimeError("MCP需要额外客户端输入或返回未支持结果类型；本次未自动继续执行。")
        return data

    @staticmethod
    def _sanitize_http_error(exc: httpx.HTTPError) -> RuntimeError:
        if isinstance(exc, httpx.HTTPStatusError):
            return RuntimeError(f"MCP HTTP 请求失败（status={exc.response.status_code}）")
        if isinstance(exc, httpx.TimeoutException):
            return RuntimeError("MCP 请求超时")
        return RuntimeError("MCP 网络请求失败")

    def _headers(self, *, include_protocol_header: bool) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if include_protocol_header:
            headers["MCP-Protocol-Version"] = self.protocol_version
        if self.session_id and self.mode == "legacy":
            headers["Mcp-Session-Id"] = self.session_id
        headers.update({key: value for key, value in self.extra_headers.items()
                        if key.lower() not in {"mcp-session-id", "mcp-protocol-version", "mcp-method", "mcp-name"}
                        and not key.lower().startswith("mcp-param-")})
        return headers

    def _capture_session_id(self, response: httpx.Response) -> None:
        session_id = (response.headers.get("Mcp-Session-Id") or "").strip()
        if session_id:
            self.session_id = session_id

    @staticmethod
    def _parse_response_text(text: str) -> dict[str, Any]:
        stripped = text.strip()
        if not stripped:
            return {}
        if stripped.startswith("event:") or "\ndata:" in stripped or stripped.startswith("data:"):
            for line in stripped.splitlines():
                if line.startswith("data:"):
                    return json.loads(line[5:].strip())
            raise RuntimeError("MCP SSE 响应缺少 data 行。")
        return json.loads(stripped)

    async def _parse_streaming_response(self, response: httpx.Response, *, request_id=None) -> dict[str, Any]:
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            pending = bytearray()
            total = 0
            event_data = []

            def event_result():
                if not event_data:
                    return None
                value = json.loads("\n".join(event_data))
                event_data.clear()
                if request_id is None or (isinstance(value, dict) and value.get("id") == request_id):
                    return value
                return None
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > self.max_response_bytes:
                    raise RuntimeError(
                        f"MCP 响应超过大小限制（max={self.max_response_bytes} bytes）。"
                    )
                pending.extend(chunk)
                while b"\n" in pending:
                    raw_line, _, remainder = pending.partition(b"\n")
                    pending = bytearray(remainder)
                    line = raw_line.rstrip(b"\r").decode(response.encoding or "utf-8")
                    if line.startswith("data:"):
                        event_data.append(line[5:].lstrip(" "))
                    elif not line:
                        value = event_result()
                        if value is not None:
                            return value
            if pending:
                line = bytes(pending).rstrip(b"\r").decode(response.encoding or "utf-8")
                if line.startswith("data:"):
                    event_data.append(line[5:].lstrip(" "))
            value = event_result()
            if value is not None:
                return value
            raise RuntimeError("MCP SSE 响应缺少 data 行。")
        content_length = response.headers.get("content-length")
        if content_length and content_length.isdigit() and int(content_length) > self.max_response_bytes:
            raise RuntimeError(
                f"MCP 响应超过大小限制（max={self.max_response_bytes} bytes）。"
            )
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > self.max_response_bytes:
                raise RuntimeError(
                    f"MCP 响应超过大小限制（max={self.max_response_bytes} bytes）。"
                )
        return self._parse_response_text(bytes(body).decode(response.encoding or "utf-8"))
