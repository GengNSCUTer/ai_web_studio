from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from app.core.config import settings


@dataclass
class McpTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    raw: dict[str, Any]


@dataclass
class McpCallResponse:
    raw: dict[str, Any]


class McpHttpClient:
    """Minimal Streamable HTTP MCP client for read-only tools."""

    protocol_version = "2025-03-26"

    def __init__(
        self,
        *,
        endpoint: str,
        timeout_seconds: int | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.timeout_seconds = timeout_seconds or settings.external_tool_timeout_seconds
        self.extra_headers = extra_headers or {}

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
                raw=tool,
            )
            for tool in tools
            if tool.get("name")
        ]

    async def call_tool(self, *, tool_name: str, arguments: dict[str, Any]) -> McpCallResponse:
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            await self._initialize(client)
            data = await self._post_jsonrpc(
                client,
                request_id=3,
                method="tools/call",
                params={"name": tool_name, "arguments": arguments},
            )
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
        await client.post(
            self.endpoint,
            headers=self._headers(include_protocol_header=True),
            json={"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        )

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
        async with client.stream(
            "POST",
            self.endpoint,
            headers=self._headers(include_protocol_header=include_protocol_header),
            json=payload,
        ) as response:
            response.raise_for_status()
            data = await self._parse_streaming_response(response)
        if "error" in data:
            error = data["error"]
            message = error.get("message") if isinstance(error, dict) else str(error)
            raise RuntimeError(f"MCP 调用失败：{message}")
        return data

    def _headers(self, *, include_protocol_header: bool) -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if include_protocol_header:
            headers["MCP-Protocol-Version"] = self.protocol_version
        headers.update(self.extra_headers)
        return headers

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

    @classmethod
    async def _parse_streaming_response(cls, response: httpx.Response) -> dict[str, Any]:
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" in content_type:
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    return json.loads(line[5:].strip())
            raise RuntimeError("MCP SSE 响应缺少 data 行。")
        body = await response.aread()
        return cls._parse_response_text(body.decode(response.encoding or "utf-8"))
