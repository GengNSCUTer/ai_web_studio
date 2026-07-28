from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from uuid import uuid4

from app.services.tools.bindings import ToolResultBindingError, ToolResultBindingResolver
from app.services.tools.executor import ToolExecutor
from app.services.tools.catalog import ToolCatalog
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
    succeeded: bool = False
    sources: list[ExternalSource] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    events: list[ToolTraceEvent] = field(default_factory=list)
    error_message: str = ""


class ToolWorkflowService:
    """执行一份 ToolPlan 内有上限的调用图。

    这里负责依赖顺序、并行、单次计划的重复调用抑制、fallback 和 Trace。
    外层 ExternalContextService 才负责最多两轮的 observe -> re-plan。
    """

    max_tool_calls = 5

    def __init__(
        self,
        *,
        executor: ToolExecutor,
        registry: ToolCatalog,
        max_tool_calls: int | None = None,
        binding_resolver: ToolResultBindingResolver | None = None,
    ) -> None:
        self.executor = executor
        self.registry = registry
        self.binding_resolver = binding_resolver or ToolResultBindingResolver()
        if max_tool_calls is not None:
            self.max_tool_calls = max_tool_calls

    async def run(self, *, plan: ToolPlan, query: str) -> ToolWorkflowResult:
        started = time.perf_counter()
        result = ToolWorkflowResult(selected_tool=plan.calls[0].category if plan.calls else "none")
        calls = plan.calls[: self.max_tool_calls]
        fallback_call_ids = self._select_fallback_call_ids(plan=plan, calls=calls, query=query)
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

        # 只抑制当前 ToolPlan 内的同工具同参数重复调用；下一轮重新规划会创建新的 Workflow。
        seen_call_keys: set[tuple[str, str]] = set()
        pending: dict[str, PlannedToolCall] = {}
        for call in calls:
            if call.call_id in pending:
                result.events.append(
                    ToolTraceEvent(
                        type="tool_workflow_step_skipped",
                        payload={
                            "workflow": "tool_workflow_v1",
                            "call_id": call.call_id,
                            "tool_key": call.tool_key,
                            "reason": "duplicate_call_id",
                        },
                    )
                )
                continue
            pending[call.call_id] = call
        completed: set[str] = set()
        failed: set[str] = set()
        sources_by_call_id: dict[str, list[ExternalSource]] = {}
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
                failed_dependencies = sorted(dep for dep in call.depends_on if dep in failed)
                if failed_dependencies:
                    result.events.append(
                        ToolTraceEvent(
                            type="tool_workflow_step_skipped",
                            payload={
                                "workflow": "tool_workflow_v1",
                                "call_id": call.call_id,
                                "tool_key": call.tool_key,
                                "depends_on": call.depends_on,
                                "failed_dependencies": failed_dependencies,
                                "reason": "failed_dependencies",
                            },
                        )
                    )
                    completed.add(call.call_id)
                    failed.add(call.call_id)
                    pending.pop(call.call_id, None)
                    continue
                if call.result_bindings:
                    definition = self.registry.get_or_none(call.tool_key)
                    try:
                        if not definition:
                            raise ToolResultBindingError("结果绑定需要可验证的工具定义。")
                        call, binding_events = self.binding_resolver.resolve(
                            call=call,
                            sources_by_call_id=sources_by_call_id,
                            definition=definition,
                        )
                        result.events.extend(binding_events)
                    except ToolResultBindingError:
                        result.events.append(
                            ToolTraceEvent(
                                type="tool_result_binding",
                                payload={
                                    "call_id": call.call_id,
                                    "tool_key": call.tool_key,
                                    "status": "failed",
                                    "reason": "结果绑定缺失、越界或不符合 Input Schema。",
                                },
                            )
                        )
                        completed.add(call.call_id)
                        failed.add(call.call_id)
                        pending.pop(call.call_id, None)
                        continue
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
                    failed.add(call.call_id)
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
                *[
                    self._execute_call(
                        call=call,
                        query=query,
                        plan=plan,
                        allow_fallback=call.call_id in fallback_call_ids,
                    )
                    for call in executable
                ]
            )
            for step_result in step_results:
                result.events.extend(step_result.events)
                result.sources.extend(step_result.sources)
                result.notices.extend(step_result.notices)
                result.selected_tool = step_result.call.category
                result.error_message = step_result.error_message or result.error_message
                completed.add(step_result.call.call_id)
                if not step_result.succeeded:
                    failed.add(step_result.call.call_id)
                sources_by_call_id[step_result.call.call_id] = list(step_result.sources)
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

    async def _execute_call(
        self,
        *,
        call: PlannedToolCall,
        query: str,
        plan: ToolPlan,
        allow_fallback: bool,
    ) -> ToolStepResult:
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
        try:
            call_result, call_events = await self.executor.execute(call)
        except Exception:
            # 凭证解析、Catalog 或策略检查也可能在 Executor 的 Adapter try 之外抛错。
            # 单个工具故障不能击穿整个并行批次，且 Trace 不记录可能含 URL/凭证的原始异常。
            safe_error = f"{call.display_name}调用失败，请稍后重试。"
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
                        "elapsed_ms": 0,
                        "error": safe_error,
                    },
                )
            )
            return ToolStepResult(call=call, events=events, error_message=safe_error)
        events.extend(call_events)
        if call_result.sources:
            return ToolStepResult(call=call, succeeded=True, sources=call_result.sources, events=events)

        error_message = call_result.error_message or ""
        if any(event.type == "tool_confirmation_required" for event in call_events):
            return ToolStepResult(
                call=call,
                notices=[f"{call.display_name}需要用户确认，已跳过执行。"],
                events=events,
                error_message=error_message,
            )

        if not plan.fallback_tool_key or not allow_fallback:
            return ToolStepResult(call=call, events=events, error_message=error_message)

        fallback_call = self._build_fallback_call(
            query=query,
            parent_call=call,
            fallback_tool_key=plan.fallback_tool_key,
        )
        try:
            fallback_result, fallback_events = await self.executor.execute(fallback_call)
        except Exception:
            safe_error = f"{fallback_call.display_name}调用失败，请稍后重试。"
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
                    ToolTraceEvent(
                        type="tool_call_error",
                        payload={
                            "call_id": fallback_call.call_id,
                            "tool_key": fallback_call.tool_key,
                            "provider": fallback_call.provider,
                            "category": fallback_call.category,
                            "display_name": fallback_call.display_name,
                            "status": "error",
                            "elapsed_ms": 0,
                            "error": safe_error,
                        },
                    ),
                ]
            )
            return ToolStepResult(call=call, events=events, error_message=safe_error)
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
        # 网页 fallback 可以给最终回答补充来源，但不等于满足天气/路线等结构化输出合同。
        # 只有同一 category 的降级工具才允许解锁依赖当前主调用的下游步骤。
        dependency_contract_satisfied = bool(fallback_result.sources) and fallback_call.category == call.category
        return ToolStepResult(
            call=call,
            succeeded=dependency_contract_satisfied,
            sources=fallback_result.sources,
            notices=notices,
            events=events,
            error_message=fallback_result.error_message or error_message,
        )

    def _select_fallback_call_ids(
        self,
        *,
        plan: ToolPlan,
        calls: list[PlannedToolCall],
        query: str,
    ) -> set[str]:
        """Reserve fallback slots before parallel execution.

        Primary calls and fallbacks share the same hard budget. A fallback cannot call
        the same tool again, duplicate an already planned call, or duplicate another
        fallback in the same plan.
        """
        if not plan.fallback_tool_key:
            return set()
        if not self.registry.get_or_none(plan.fallback_tool_key):
            return set()
        remaining_slots = max(0, self.max_tool_calls - len(calls))
        if remaining_slots == 0:
            return set()

        reserved_keys = {(call.tool_key, self._stable_arguments(call)) for call in calls}
        allowed: set[str] = set()
        for call in calls:
            if len(allowed) >= remaining_slots or call.tool_key == plan.fallback_tool_key:
                continue
            fallback_call = self._build_fallback_call(
                query=query,
                parent_call=call,
                fallback_tool_key=plan.fallback_tool_key,
            )
            fallback_key = (fallback_call.tool_key, self._stable_arguments(fallback_call))
            if fallback_key in reserved_keys:
                continue
            reserved_keys.add(fallback_key)
            allowed.add(call.call_id)
        return allowed

    def _build_fallback_call(
        self,
        *,
        query: str,
        parent_call: PlannedToolCall,
        fallback_tool_key: str,
    ) -> PlannedToolCall:
        definition = self.registry.get(fallback_tool_key)
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
        return json.dumps(call.arguments, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
