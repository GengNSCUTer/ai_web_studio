from __future__ import annotations

import time
from uuid import uuid4

from app.services.tools.executor import ToolExecutor
from app.services.tools.formatter import ExternalContextAssembler
from app.services.tools.registry import ToolRegistry
from app.services.tools.router import RuleBasedToolRouter
from app.services.tools.schemas import (
    ExternalContextResult,
    ExternalSource,
    PlannedToolCall,
    ToolPlan,
    ToolTraceEvent,
)


class ExternalContextService:
    """Facade for external context retrieval.

    Chat routes should depend on this facade only. Tool definitions, routing,
    execution and prompt assembly live in app.services.tools.
    """

    def __init__(
        self,
        *,
        registry: ToolRegistry | None = None,
        router: RuleBasedToolRouter | None = None,
        executor: ToolExecutor | None = None,
        assembler: ExternalContextAssembler | None = None,
    ) -> None:
        self.registry = registry or ToolRegistry()
        self.router = router or RuleBasedToolRouter(self.registry)
        self.executor = executor or ToolExecutor()
        self.assembler = assembler or ExternalContextAssembler()

    async def build_context(self, *, query: str, enabled: bool, max_chars: int) -> ExternalContextResult:
        plan = self.router.plan(query=query, enabled=enabled)
        if not enabled or not plan.should_use_tools:
            return ExternalContextResult(
                context_text=None,
                sources=[],
                notices=[],
                diagnostics={
                    "external_context_enabled": 0,
                    "external_tool_called": "none",
                    "external_sources_total": 0,
                    "external_sources_included": 0,
                    "external_context_chars": 0,
                    "external_context_error": 0,
                },
                details={
                    "external_sources": [],
                    "tool_plan": plan.to_public_dict(),
                    "tool_events": [],
                },
                tool_plan=plan,
                tool_events=[],
            )

        started = time.perf_counter()
        notices: list[str] = []
        sources: list[ExternalSource] = []
        events: list[ToolTraceEvent] = [
            ToolTraceEvent(
                type="tool_plan",
                payload={"plan": plan.to_public_dict()},
            )
        ]
        selected_tool = plan.calls[0].category if plan.calls else "none"
        error_message = ""

        for call in plan.calls[:1]:
            result, call_events = await self.executor.execute(call)
            events.extend(call_events)
            selected_tool = call.category
            sources = result.sources
            error_message = result.error_message or ""

            if sources or not plan.fallback_tool_key:
                break

            notices.append(f"{call.display_name}未返回有效结果，已回退到网页搜索。")
            fallback_call = self._build_fallback_call(query=query, parent_call=call)
            fallback_result, fallback_events = await self.executor.execute(fallback_call)
            events.extend(
                [
                    ToolTraceEvent(
                        type="tool_call_fallback",
                        payload={
                            "from_call_id": call.call_id,
                            "from_tool_key": call.tool_key,
                            "to_call_id": fallback_call.call_id,
                            "to_tool_key": fallback_call.tool_key,
                            "reason": "primary_tool_empty_or_failed",
                        },
                    ),
                    *fallback_events,
                ]
            )
            selected_tool = fallback_call.category
            sources = fallback_result.sources
            error_message = fallback_result.error_message or error_message
            break

        if error_message and not sources:
            notices.append(f"外部信息工具调用失败：{error_message}")

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        context_text = self.assembler.format_sources_for_prompt(sources, max_chars=max_chars)
        included_sources = sources if context_text else []
        public_sources = [source.to_public_dict() for source in sources]
        public_events = [event.to_public_dict() for event in events]

        return ExternalContextResult(
            context_text=context_text,
            sources=sources,
            notices=notices,
            diagnostics={
                "external_context_enabled": 1,
                "external_tool_called": selected_tool,
                "external_sources_total": len(sources),
                "external_sources_included": len(included_sources),
                "external_context_chars": len(context_text or ""),
                "external_context_latency_ms": elapsed_ms,
                "external_context_error": int(bool(error_message and not sources)),
                "external_tool_events_total": len(events),
            },
            details={
                "external_sources": public_sources,
                "tool_plan": plan.to_public_dict(),
                "tool_events": public_events,
            },
            tool_plan=plan,
            tool_events=events,
        )

    def _build_fallback_call(self, *, query: str, parent_call: PlannedToolCall) -> PlannedToolCall:
        definition = self.registry.web_search_tool()
        return PlannedToolCall(
            call_id=str(uuid4()),
            tool_key=definition.tool_key,
            provider=definition.provider,
            category=definition.category,
            display_name=definition.display_name,
            confidence=0.62,
            reason=f"{parent_call.display_name}未返回有效结果或调用失败，回退到网页搜索。",
            arguments={"query": query},
        )
