from __future__ import annotations

import time

from app.services.tools.providers.amap import AmapToolProvider
from app.services.tools.providers.tavily import TavilySearchProvider
from app.services.tools.schemas import PlannedToolCall, ToolCallResult, ToolTraceEvent


class ToolExecutor:
    def __init__(
        self,
        *,
        tavily_provider: TavilySearchProvider | None = None,
        amap_provider: AmapToolProvider | None = None,
    ) -> None:
        self.tavily_provider = tavily_provider or TavilySearchProvider()
        self.amap_provider = amap_provider or AmapToolProvider()

    async def execute(self, call: PlannedToolCall) -> tuple[ToolCallResult, list[ToolTraceEvent]]:
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
                },
            )
        ]
        started = time.perf_counter()
        try:
            query = str(call.arguments.get("query") or "")
            if call.tool_key == "web.tavily.search":
                sources = await self.tavily_provider.query(query)
            elif call.tool_key == "amap.weather.current":
                sources = await self.amap_provider.query_weather(query)
            elif call.tool_key == "amap.map.basic":
                sources = await self.amap_provider.query_map(query)
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
