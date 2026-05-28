from __future__ import annotations

import time

from app.services.tools.credentials import ToolCredentialResolver
from app.services.tools.providers.amap import AmapToolProvider
from app.services.tools.providers.tavily import TavilySearchProvider
from app.services.tools.schemas import PlannedToolCall, ToolCallResult, ToolTraceEvent


class ToolExecutor:
    def __init__(
        self,
        *,
        tavily_provider: TavilySearchProvider | None = None,
        amap_provider: AmapToolProvider | None = None,
        credential_resolver: ToolCredentialResolver | None = None,
        user_id: str | None = None,
        project_id: str | None = None,
    ) -> None:
        self.tavily_provider = tavily_provider or TavilySearchProvider()
        self.amap_provider = amap_provider or AmapToolProvider()
        self.credential_resolver = credential_resolver or ToolCredentialResolver()
        self.user_id = user_id
        self.project_id = project_id

    async def execute(self, call: PlannedToolCall) -> tuple[ToolCallResult, list[ToolTraceEvent]]:
        if not self.credential_resolver.is_tool_enabled_for_workspace(
            project_id=self.project_id,
            tool_key=call.tool_key,
        ):
            return self._skipped(call, "当前工作区已禁用该工具。")

        credential = self.credential_resolver.resolve(user_id=self.user_id, provider_key=call.provider)
        if not credential.is_enabled:
            return self._skipped(call, f"工具 provider {call.provider} 未启用或未配置凭证。")

        events = [
            ToolTraceEvent(
                type="tool_call_start",
                payload={
                    "call_id": call.call_id,
                    "tool_key": call.tool_key,
                    "provider": call.provider,
                    "category": call.category,
                    "display_name": call.display_name,
                    "arguments": call.arguments,
                    "credential_source": credential.source,
                },
            )
        ]
        started = time.perf_counter()
        try:
            query = str(call.arguments.get("query") or "")
            if call.tool_key == "web.tavily.search":
                sources = await self.tavily_provider.query(query, api_key=credential.api_key)
            elif call.tool_key == "amap.weather.current":
                sources = await self.amap_provider.query_weather(query, api_key=credential.api_key)
            elif call.tool_key == "amap.map.basic":
                sources = await self.amap_provider.query_map(query, api_key=credential.api_key)
            else:
                raise RuntimeError(f"未知工具：{call.tool_key}")

            elapsed_ms = int((time.perf_counter() - started) * 1000)
            result = ToolCallResult(
                call=call,
                status="success",
                sources=sources,
                elapsed_ms=elapsed_ms,
            )
            events.append(
                ToolTraceEvent(
                    type="tool_call_end",
                    payload={
                        "call_id": call.call_id,
                        "tool_key": call.tool_key,
                        "provider": call.provider,
                        "category": call.category,
                        "display_name": call.display_name,
                        "status": "success",
                        "elapsed_ms": elapsed_ms,
                        "sources_count": len(sources),
                    },
                )
            )
            return result, events
        except Exception as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            result = ToolCallResult(
                call=call,
                status="error",
                sources=[],
                elapsed_ms=elapsed_ms,
                error_message=str(exc),
            )
            events.append(
                ToolTraceEvent(
                    type="tool_call_error",
                    payload={
                        "call_id": call.call_id,
                        "tool_key": call.tool_key,
                        "provider": call.provider,
                        "category": call.category,
                        "display_name": call.display_name,
                        "status": "error",
                        "elapsed_ms": elapsed_ms,
                        "error": str(exc),
                    },
                )
            )
            return result, events

    @staticmethod
    def _skipped(call: PlannedToolCall, reason: str) -> tuple[ToolCallResult, list[ToolTraceEvent]]:
        result = ToolCallResult(
            call=call,
            status="skipped",
            sources=[],
            elapsed_ms=0,
            error_message=reason,
        )
        events = [
            ToolTraceEvent(
                type="tool_call_error",
                payload={
                    "call_id": call.call_id,
                    "tool_key": call.tool_key,
                    "provider": call.provider,
                    "category": call.category,
                    "display_name": call.display_name,
                    "status": "skipped",
                    "elapsed_ms": 0,
                    "error": reason,
                },
            )
        ]
        return result, events
