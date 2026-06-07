from __future__ import annotations

import time
import asyncio
from dataclasses import dataclass, field
from uuid import uuid4

from app.services.tools.executor import ToolExecutor
from app.services.tools.registry import ToolRegistry
from app.services.tools.schemas import ExternalSource, PlannedToolCall, ToolPlan, ToolTraceEvent


@dataclass
class ToolWorkflowResult:
    sources: list[ExternalSource] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    events: list[ToolTraceEvent] = field(default_factory=list)
    selected_tool: str = "none"
    error_message: str = ""
    elapsed_ms: int = 0


@dataclass
class ToolStepResult:
    call: PlannedToolCall
    sources: list[ExternalSource] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    events: list[ToolTraceEvent] = field(default_factory=list)
    error_message: str = ""


class ToolWorkflowService:
    """Executes a bounded sequence of planned tool calls.

    This is intentionally not a fully autonomous Agent loop yet. It supports
    multiple calls from the planner, per-call fallback, maximum tool count, and
    trace visibility. A later version can add "observe result -> ask planner
    again" semantics on top of this boundary.
    """

    max_tool_calls = 5

    def __init__(
        self,
        *,
        executor: ToolExecutor,
        registry: ToolRegistry,
        max_tool_calls: int | None = None,
    ) -> None:
        self.executor = executor
        self.registry = registry
        if max_tool_calls is not None:
            self.max_tool_calls = max_tool_calls

    async def run(self, *, plan: ToolPlan, query: str) -> ToolWorkflowResult:
        started = time.perf_counter()
        result = ToolWorkflowResult(selected_tool=plan.calls[0].category if plan.calls else "none")
        calls = plan.calls[: self.max_tool_calls]
        result.events.append(
            ToolTraceEvent(
                type="tool_workflow_start",
                payload={
                    "workflow": "tool_workflow_v1",
                    "plan_id": plan.plan_id,
                    "planned_calls": len(plan.calls),
                    "max_tool_calls": self.max_tool_calls,
                    "executing_calls": len(calls),
                },
            )
        )

        seen_call_keys: set[tuple[str, str]] = set()
        pending = {call.call_id: call for call in calls}
        completed: set[str] = set()
        step = 0
        while pending:
            ready = [
                call
                for call in pending.values()
                if all(dep in completed for dep in call.depends_on)
            ]
            if not ready:
                for call in pending.values():
                    result.events.append(
                        ToolTraceEvent(
                            type="tool_workflow_step_skipped",
                            payload={
                                "workflow": "tool_workflow_v1",
                                "call_id": call.call_id,
                                "tool_key": call.tool_key,
                                "depends_on": call.depends_on,
                                "reason": "unresolved_or_cyclic_dependencies",
                            },
                        )
                    )
                break

            non_parallel = [call for call in ready if not call.can_parallel]
            if non_parallel:
                ready = [non_parallel[0]]

            executable: list[PlannedToolCall] = []
            for call in ready:
                call_key = (call.tool_key, self._stable_arguments(call))
                if call_key in seen_call_keys:
                    result.events.append(
                        ToolTraceEvent(
                            type="tool_workflow_step_skipped",
                            payload={
                                "workflow": "tool_workflow_v1",
                                "call_id": call.call_id,
                                "tool_key": call.tool_key,
                                "reason": "duplicate_tool_call",
                            },
                        )
                    )
                    completed.add(call.call_id)
                    pending.pop(call.call_id, None)
                    continue
                seen_call_keys.add(call_key)
                executable.append(call)

            if not executable:
                continue
            step += 1
            result.events.append(
                ToolTraceEvent(
                    type="tool_workflow_batch",
                    payload={
                        "workflow": "tool_workflow_v1",
                        "step": step,
                        "mode": "parallel" if len(executable) > 1 else "single",
                        "call_ids": [call.call_id for call in executable],
                        "tool_keys": [call.tool_key for call in executable],
                    },
                )
            )
            step_results = await asyncio.gather(
                *[self._execute_call(call=call, query=query, plan=plan) for call in executable]
            )
            for step_result in step_results:
                result.events.extend(step_result.events)
                result.sources.extend(step_result.sources)
                result.notices.extend(step_result.notices)
                result.selected_tool = step_result.call.category
                result.error_message = step_result.error_message or result.error_message
                completed.add(step_result.call.call_id)
                pending.pop(step_result.call.call_id, None)

        result.elapsed_ms = int((time.perf_counter() - started) * 1000)
        result.events.append(
            ToolTraceEvent(
                type="tool_workflow_end",
                payload={
                    "workflow": "tool_workflow_v1",
                    "plan_id": plan.plan_id,
                    "status": "success" if result.sources else "empty",
                    "elapsed_ms": result.elapsed_ms,
                    "sources_count": len(result.sources),
                    "error": result.error_message or None,
                },
            )
        )
        return result

    async def _execute_call(self, *, call: PlannedToolCall, query: str, plan: ToolPlan) -> ToolStepResult:
        events = [
            ToolTraceEvent(
                type="tool_workflow_step",
                payload={
                    "workflow": "tool_workflow_v1",
                    "call_id": call.call_id,
                    "tool_key": call.tool_key,
                    "display_name": call.display_name,
                    "depends_on": call.depends_on,
                    "can_parallel": call.can_parallel,
                    "reason": call.reason,
                },
            )
        ]
        call_result, call_events = await self.executor.execute(call)
        events.extend(call_events)
        if call_result.sources:
            return ToolStepResult(call=call, sources=call_result.sources, events=events)

        error_message = call_result.error_message or ""
        if any(event.type == "tool_confirmation_required" for event in call_events):
            return ToolStepResult(
                call=call,
                notices=[f"{call.display_name}需要用户确认，已跳过执行。"],
                events=events,
                error_message=error_message,
            )

        if not plan.fallback_tool_key:
            return ToolStepResult(call=call, events=events, error_message=error_message)

        fallback_call = self._build_fallback_call(query=query, parent_call=call)
        fallback_result, fallback_events = await self.executor.execute(fallback_call)
        events.extend(
            [
                ToolTraceEvent(
                    type="tool_call_fallback",
                    payload={
                        "from": call.tool_key,
                        "to": fallback_call.tool_key,
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
        notices = [f"{call.display_name}未返回有效结果，已回退到网页搜索。"] if fallback_result.sources else []
        return ToolStepResult(
            call=call,
            sources=fallback_result.sources,
            notices=notices,
            events=events,
            error_message=fallback_result.error_message or error_message,
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

    @staticmethod
    def _stable_arguments(call: PlannedToolCall) -> str:
        items = sorted((str(key), str(value)) for key, value in call.arguments.items())
        return "|".join(f"{key}={value}" for key, value in items)
