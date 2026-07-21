from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx
from jsonschema import Draft202012Validator, SchemaError, ValidationError

from app.core.config import settings


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


class McpHttpClient:
    """最小 Streamable HTTP MCP client：initialize 后执行 tools/list 或 tools/call。"""

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

    async def list_tools(self) -> list[McpTool]:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            await self._initialize(client)
            data = await self._post_jsonrpc(client, request_id=2, method="tools/list", params={})
        tools = ((data.get("result") or {}).get("tools") or [])
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
    ) -> McpCallResponse:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            await self._initialize(client)
            data = await self._post_jsonrpc(
                client,
                request_id=3,
                method="tools/call",
                params={"name": tool_name, "arguments": arguments},
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
        await self._post_jsonrpc(
            client,
            request_id=1,
            method="initialize",
            params={
                "protocolVersion": self.protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "ai_web_studio", "version": "0.1.0"},
            },
            include_protocol_header=False,
        )
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
        self,
        client: httpx.AsyncClient,
        *,
        request_id: int,
        method: str,
        params: dict[str, Any],
        include_protocol_header: bool = True,
    ) -> dict[str, Any]:
        payload = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        try:
            async with client.stream(
                "POST",
                self.endpoint,
                headers=self._headers(include_protocol_header=include_protocol_header),
                json=payload,
            ) as response:
                response.raise_for_status()
                self._capture_session_id(response)
                data = await self._parse_streaming_response(response)
        except httpx.HTTPError as exc:
            # httpx 的 HTTPStatusError 会包含完整请求 URL；这里在进入 Tool Trace 前去掉 endpoint/credential。
            raise self._sanitize_http_error(exc) from None
        if not isinstance(data, dict):
            raise RuntimeError("MCP JSON-RPC 响应格式不合法。")
        if "error" in data:
            error = data["error"]
            # 远端 message 是不可信文本，可能回显凭证/参数；Trace 只保留协议错误码。
            code = error.get("code") if isinstance(error, dict) else None
            suffix = f"（code={code}）" if isinstance(code, int) else ""
            raise RuntimeError(f"MCP JSON-RPC 调用失败{suffix}。")
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
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        headers.update(self.extra_headers)
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

    async def _parse_streaming_response(self, response: httpx.Response) -> dict[str, Any]:
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            pending = bytearray()
            total = 0
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
                        return json.loads(line[5:].strip())
            if pending:
                line = bytes(pending).rstrip(b"\r").decode(response.encoding or "utf-8")
                if line.startswith("data:"):
                    return json.loads(line[5:].strip())
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
