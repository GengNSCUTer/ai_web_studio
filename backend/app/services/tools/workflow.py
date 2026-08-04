from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from uuid import uuid4

from app.services.tools.bindings import ToolResultBindingError, ToolResultBindingResolver
from app.services.tools.executor import ToolExecutor
from app.services.tools.catalog import ToolCatalog
from app.services.tools.quality import (
    decide_tool_result_action,
    evaluate_tool_result_quality,
    is_usable_tool_result,
    quality_error_for_result,
    quality_status_for_result,
)
from app.services.tools.schemas import (
    ExternalSource,
    PlannedToolCall,
    ToolCallResult,
    ToolPlan,
    ToolTraceEvent,
)


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
    quality_status: str = "unknown"
    quality_reasons: list[str] = field(default_factory=list)
    quality_action: str = "block"
    sources: list[ExternalSource] = field(default_factory=list)
    # Sources from failed/blocked calls stay available for trace/debugging, but
    # only an explicitly approved result may enter the final answer context.
    expose_sources_to_prompt: bool = False
    notices: list[str] = field(default_factory=list)
    events: list[ToolTraceEvent] = field(default_factory=list)
    error_message: str = ""


class ToolWorkflowService:
    """执行一份 ToolPlan 内有上限的调用图。

    这里负责依赖顺序、并行、单次计划的重复调用抑制、fallback 和 Trace。
    外层 ExternalContextService 才负责最多五轮的 observe -> re-plan。
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
        quality_by_call_id: dict[str, str] = {}
        quality_reasons_by_call_id: dict[str, list[str]] = {}
        quality_actions_by_call_id: dict[str, str] = {}
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
                    dependency_quality = {
                        dep: quality_by_call_id.get(dep, "unknown") for dep in failed_dependencies
                    }
                    quality_blocked = any(
                        status in {"invalid", "uncertain"} for status in dependency_quality.values()
                    )
                    result.events.append(
                        ToolTraceEvent(
                            type="quality_gate_blocked" if quality_blocked else "tool_workflow_step_skipped",
                            payload={
                                "workflow": "tool_workflow_v1",
                                "call_id": call.call_id,
                                "tool_key": call.tool_key,
                                "depends_on": call.depends_on,
                                "failed_dependencies": failed_dependencies,
                                "dependency_quality": dependency_quality,
                                "dependency_quality_reasons": {
                                    dep: quality_reasons_by_call_id.get(dep, [])
                                    for dep in failed_dependencies
                                },
                                "next_action": (
                                    "replan"
                                    if any(quality_actions_by_call_id.get(dep) == "replan" for dep in failed_dependencies)
                                    else "block"
                                ),
                                "reason": "upstream_quality_not_usable"
                                if quality_blocked
                                else "failed_dependencies",
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
                if step_result.sources and not step_result.expose_sources_to_prompt:
                    suppression_type = (
                        "quality_evidence_suppressed"
                        if step_result.quality_status in {"invalid", "uncertain"}
                        else "tool_evidence_suppressed"
                    )
                    result.events.append(
                        ToolTraceEvent(
                            type=suppression_type,
                            payload={
                                "workflow": "tool_workflow_v1",
                                "call_id": step_result.call.call_id,
                                "tool_key": step_result.call.tool_key,
                                "status": step_result.quality_status,
                                "reasons": list(step_result.quality_reasons),
                                "sources_count": len(step_result.sources),
                                "exposed_to_prompt": False,
                                "reason": (
                                    "quality_gate"
                                    if suppression_type == "quality_evidence_suppressed"
                                    else "non_success_result"
                                ),
                            },
                        )
                    )
                elif step_result.expose_sources_to_prompt:
                    result.sources.extend(step_result.sources)
                result.notices.extend(step_result.notices)
                result.selected_tool = step_result.call.category
                result.error_message = step_result.error_message or result.error_message
                completed.add(step_result.call.call_id)
                if not step_result.succeeded:
                    failed.add(step_result.call.call_id)
                quality_by_call_id[step_result.call.call_id] = step_result.quality_status
                quality_reasons_by_call_id[step_result.call.call_id] = list(step_result.quality_reasons)
                quality_actions_by_call_id[step_result.call.call_id] = step_result.quality_action
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
        error_message = call_result.error_message or ""
        quality_status, quality_reasons = self._quality_for_result(call_result)
        result_usable = self._result_is_usable(call_result)
        confirmation_required = any(event.type == "tool_confirmation_required" for event in call_events)
        decision_status = (
            "uncertain"
            if confirmation_required
            else ("valid" if quality_status == "unknown" and result_usable else quality_status)
        )
        definition = self.registry.get_or_none(call.tool_key)
        fallback_available = bool(
            plan.fallback_tool_key
            and allow_fallback
            and self._fallback_definition_is_safe(plan.fallback_tool_key)
        )
        quality_decision = decide_tool_result_action(
            status=decision_status,
            reasons=quality_reasons,
            retryable=call_result.retryable,
            fallback_available=fallback_available,
            # Sync Tool Workflow has no hidden retry budget. Durable Runtime owns
            # retry attempts; this path can only fallback or request re-planning.
            retry_allowed=False,
            risk_level=definition.risk_level if definition else "high",
            read_only=definition.read_only if definition else False,
        )
        events.append(
            ToolTraceEvent(
                type="tool_result_quality_decision",
                payload={
                    "workflow": "tool_workflow_v1",
                    "call_id": call.call_id,
                    "tool_key": call.tool_key,
                    "status": quality_decision.status,
                    "action": quality_decision.action,
                    "retryable": quality_decision.retryable,
                    "fallback_available": quality_decision.fallback_available,
                    "reasons": list(quality_decision.reasons),
                },
            )
        )
        if confirmation_required:
            return ToolStepResult(
                call=call,
                # Diff 作为 evidence 告知最终模型“等待确认”，但该 Step 仍失败关闭，
                # 依赖实际写入结果的下游步骤不会被解锁。
                sources=call_result.sources,
                expose_sources_to_prompt=True,
                quality_status="uncertain",
                quality_reasons=[*quality_reasons, "user_confirmation_required"],
                quality_action="clarify",
                notices=[f"{call.display_name}需要用户确认，已跳过执行。"],
                events=events,
                error_message=error_message,
            )

        if result_usable:
            return ToolStepResult(
                call=call,
                succeeded=True,
                quality_status=quality_status,
                quality_reasons=quality_reasons,
                quality_action="continue",
                sources=call_result.sources,
                expose_sources_to_prompt=True,
                events=events,
            )

        if call_result.status == "success" and quality_status in {"invalid", "uncertain"}:
            events.append(
                ToolTraceEvent(
                    type="quality_gate_blocked",
                    payload={
                        "workflow": "tool_workflow_v1",
                        "call_id": call.call_id,
                        "tool_key": call.tool_key,
                        "status": quality_status,
                        "reasons": quality_reasons,
                        "next_action": quality_decision.action,
                        "downstream_unlocked": False,
                    },
                )
            )

        if quality_decision.action != "fallback" or not plan.fallback_tool_key or not allow_fallback:
            return ToolStepResult(
                call=call,
                quality_status=quality_status,
                quality_reasons=quality_reasons,
                quality_action=quality_decision.action,
                sources=call_result.sources,
                events=events,
                error_message=error_message or self._quality_error(call_result),
            )

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
            return ToolStepResult(
                call=call,
                quality_status=quality_status,
                quality_reasons=quality_reasons,
                sources=call_result.sources,
                events=events,
                error_message=safe_error,
            )
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
        fallback_quality_status, fallback_quality_reasons = self._quality_for_result(fallback_result)
        fallback_usable = self._result_is_usable(fallback_result)
        fallback_decision_status = (
            "valid"
            if fallback_quality_status == "unknown" and fallback_usable
            else fallback_quality_status
        )
        fallback_definition = self.registry.get_or_none(fallback_call.tool_key)
        fallback_decision = decide_tool_result_action(
            status=fallback_decision_status,
            reasons=fallback_quality_reasons,
            retryable=fallback_result.retryable,
            fallback_available=False,
            retry_allowed=False,
            risk_level=fallback_definition.risk_level if fallback_definition else "high",
            read_only=fallback_definition.read_only if fallback_definition else False,
        )
        events.append(
            ToolTraceEvent(
                type="tool_result_quality_decision",
                payload={
                    "workflow": "tool_workflow_v1",
                    "call_id": fallback_call.call_id,
                    "tool_key": fallback_call.tool_key,
                    "status": fallback_decision.status,
                    "action": fallback_decision.action,
                    "retryable": fallback_decision.retryable,
                    "fallback_available": False,
                    "reasons": list(fallback_decision.reasons),
                    "stage": "fallback",
                },
            )
        )
        notices = [f"{call.display_name}未返回可用结果，已回退到网页搜索。"] if fallback_usable else []
        # 网页 fallback 可以给最终回答补充来源，但不等于满足天气/路线等结构化输出合同。
        # 只有同一 category 且通过主工具质量合同的降级工具才允许解锁下游。
        dependency_contract_satisfied = fallback_usable and fallback_call.category == call.category
        parent_definition = self.registry.get_or_none(call.tool_key)
        parent_contract = parent_definition.quality_contract if parent_definition else {}
        fallback_dependency_quality = evaluate_tool_result_quality(
            sources=fallback_result.sources,
            contract=parent_contract,
        )
        if dependency_contract_satisfied and fallback_dependency_quality.status != "valid":
            dependency_contract_satisfied = False
            events.append(
                ToolTraceEvent(
                    type="quality_gate_blocked",
                    payload={
                        "workflow": "tool_workflow_v1",
                        "call_id": call.call_id,
                        "tool_key": call.tool_key,
                        "stage": "fallback_dependency_contract",
                        "status": fallback_dependency_quality.status,
                        "reasons": list(fallback_dependency_quality.reasons),
                        "downstream_unlocked": False,
                    },
                )
            )
        return ToolStepResult(
            call=call,
            succeeded=dependency_contract_satisfied,
            quality_status=fallback_quality_status,
            quality_reasons=fallback_quality_reasons,
            quality_action=("continue" if dependency_contract_satisfied else "block"),
            sources=fallback_result.sources,
            expose_sources_to_prompt=fallback_usable,
            notices=notices,
            events=events,
            error_message=fallback_result.error_message or error_message or self._quality_error(fallback_result),
        )

    @staticmethod
    def _quality_for_result(call_result: ToolCallResult) -> tuple[str, list[str]]:
        return quality_status_for_result(call_result)

    @classmethod
    def _result_is_usable(cls, call_result: ToolCallResult) -> bool:
        return is_usable_tool_result(call_result)

    @staticmethod
    def _quality_error(call_result: ToolCallResult) -> str:
        return quality_error_for_result(call_result)

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
        if not self._fallback_definition_is_safe(plan.fallback_tool_key):
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

    def _fallback_definition_is_safe(self, tool_key: str) -> bool:
        """Only low-risk read-only tools may be invoked as an automatic fallback."""

        definition = self.registry.get_or_none(tool_key)
        return bool(definition and definition.read_only and definition.risk_level == "low")

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
