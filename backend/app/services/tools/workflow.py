from __future__ import annotations

import asyncio
from hashlib import sha256
import json
import time
from dataclasses import dataclass, field
from typing import Any
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


@dataclass(frozen=True)
class ToolWorkflowAggregate:
    """一份同步工具计划的安全汇总结果。

    单个 Step 的 Outcome 用于判断依赖和追踪；本对象则回答更直接的问题：
    整个任务是否全部完成、是否只能部分回答、是否在等待用户确认，以及
    哪些类别的步骤没有完成。它不保存参数、Provider 原始异常或工具正文，
    因而可以安全写入 Trace、诊断信息和后续的外层策略。
    """

    status: str = "empty"
    total_steps: int = 0
    attempted_steps: int = 0
    executed_successfully_steps: int = 0
    completed_valid_steps: int = 0
    prompt_eligible_steps: int = 0
    failed_steps: int = 0
    blocked_steps: int = 0
    independent_failed_steps: int = 0
    independent_blocked_steps: int = 0
    dependent_failed_steps: int = 0
    dependency_blocked_steps: int = 0
    waiting_approval_steps: int = 0
    invalid_quality_steps: int = 0
    uncertain_quality_steps: int = 0

    @classmethod
    def from_outcomes(cls, outcomes: list["ToolStepOutcome"]) -> "ToolWorkflowAggregate":
        """从终态 Step 计算稳定的任务状态和无敏感计数摘要。

        状态优先级刻意保守：只要存在已完成且质量有效的步骤，独立分支的
        失败不会抹掉这些可靠结果，整体记为 ``partial``；但等待确认本身不
        能被当成“已完成”，它在没有已完成结果时维持 ``waiting_approval``。
        """

        if not outcomes:
            return cls()

        completed_valid_steps = sum(outcome.unlocks_strict_dependents for outcome in outcomes)
        attempted_steps = sum(
            outcome.execution_status
            in {"succeeded", "failed", "waiting_approval", "cancelled", "timed_out"}
            for outcome in outcomes
        )
        executed_successfully_steps = sum(
            outcome.execution_status == "succeeded" for outcome in outcomes
        )
        prompt_eligible_steps = sum(outcome.prompt_eligible for outcome in outcomes)
        failed_steps = sum(
            outcome.execution_status in {"failed", "timed_out", "cancelled"}
            for outcome in outcomes
        )
        blocked_steps = sum(outcome.execution_status == "blocked" for outcome in outcomes)
        independent_failed_steps = sum(
            outcome.execution_status in {"failed", "timed_out", "cancelled"}
            and not outcome.depends_on
            for outcome in outcomes
        )
        dependency_blocked_steps = sum(
            outcome.execution_status == "blocked" and bool(outcome.depends_on)
            for outcome in outcomes
        )
        dependent_failed_steps = sum(
            outcome.execution_status in {"failed", "timed_out", "cancelled"}
            and bool(outcome.depends_on)
            for outcome in outcomes
        )
        independent_blocked_steps = sum(
            outcome.execution_status == "blocked" and not outcome.depends_on
            for outcome in outcomes
        )
        waiting_approval_steps = sum(
            outcome.execution_status == "waiting_approval" for outcome in outcomes
        )
        invalid_quality_steps = sum(outcome.quality_status == "invalid" for outcome in outcomes)
        uncertain_quality_steps = sum(outcome.quality_status == "uncertain" for outcome in outcomes)

        if completed_valid_steps:
            status = "succeeded" if completed_valid_steps == len(outcomes) else "partial"
        elif waiting_approval_steps:
            status = "waiting_approval"
        elif prompt_eligible_steps:
            # 该分支主要覆盖兼容性的只读预览结果。它有可展示信息，但不能
            # 被误写成所有步骤均已完成。
            status = "partial"
        elif failed_steps:
            status = "failed"
        elif blocked_steps:
            status = "blocked"
        else:
            status = "empty"

        return cls(
            status=status,
            total_steps=len(outcomes),
            attempted_steps=attempted_steps,
            executed_successfully_steps=executed_successfully_steps,
            completed_valid_steps=completed_valid_steps,
            prompt_eligible_steps=prompt_eligible_steps,
            failed_steps=failed_steps,
            blocked_steps=blocked_steps,
            independent_failed_steps=independent_failed_steps,
            independent_blocked_steps=independent_blocked_steps,
            dependent_failed_steps=dependent_failed_steps,
            dependency_blocked_steps=dependency_blocked_steps,
            waiting_approval_steps=waiting_approval_steps,
            invalid_quality_steps=invalid_quality_steps,
            uncertain_quality_steps=uncertain_quality_steps,
        )

    def to_trace_payload(self) -> dict[str, int | str | bool]:
        """返回可安全暴露给诊断和前端的汇总信息。"""

        return {
            "status": self.status,
            "total_steps": self.total_steps,
            "attempted_steps": self.attempted_steps,
            "executed_successfully_steps": self.executed_successfully_steps,
            "completed_valid_steps": self.completed_valid_steps,
            "prompt_eligible_steps": self.prompt_eligible_steps,
            "failed_steps": self.failed_steps,
            "blocked_steps": self.blocked_steps,
            "independent_failed_steps": self.independent_failed_steps,
            "independent_blocked_steps": self.independent_blocked_steps,
            "dependent_failed_steps": self.dependent_failed_steps,
            "dependency_blocked_steps": self.dependency_blocked_steps,
            "waiting_approval_steps": self.waiting_approval_steps,
            "invalid_quality_steps": self.invalid_quality_steps,
            "uncertain_quality_steps": self.uncertain_quality_steps,
            "has_reliable_completed_result": self.completed_valid_steps > 0,
            "has_pending_user_confirmation": self.waiting_approval_steps > 0,
            # 独立分支失败只影响自身；带依赖的 blocked Step 不得被当成
            # 可以脱离上游继续执行的普通失败。
            "has_independent_soft_failure": independent_failure_exists(self),
            "has_strict_dependency_block": self.dependency_blocked_steps > 0,
            "has_dependency_branch_failure": bool(
                self.dependent_failed_steps or self.dependency_blocked_steps
            ),
        }


@dataclass(frozen=True)
class ToolWorkflowAction:
    """外层 Chat 对一次工具聚合结果采取的确定动作。

    该决策由代码侧状态和预算决定，不接受 Planner 或 Tool 返回内容的直接改写。
    ``finalize_partial`` 表示仅使用已经通过质量门的证据；``replan`` 表示在
    剩余轮次内允许 Planner 基于脱敏观察重新规划；``clarify`` 需要用户补充或
    确认；``stop`` 则是本次同步请求的终止状态。
    """

    action: str
    reason: str
    notice: str = ""


def decide_workflow_action(
    *,
    aggregate: ToolWorkflowAggregate,
    has_sources: bool,
    has_error: bool = False,
    planner_need_more_rounds: bool,
    quality_replan_required: bool,
    round_index: int,
    max_rounds: int,
) -> ToolWorkflowAction:
    """把工具聚合状态映射为同步 Chat 的下一步动作。

    ``has_sources`` 只用于兼容自定义 Workflow；正式 Workflow 的 source 都已经
    经过质量门。任何需要继续的动作都必须同时满足五轮预算，否则明确停止。
    """

    if aggregate.waiting_approval_steps:
        return ToolWorkflowAction(
            action="clarify",
            reason="waiting_approval",
            notice="有工具操作需要你的确认，已暂停后续执行。",
        )

    # 严格依赖阻断是代码侧安全结论。Planner 即使继续请求工具，也不能把
    # 未满足的上游关系当成可选项绕过；先让用户补充/确认依赖信息。
    if aggregate.status == "blocked" and aggregate.dependency_blocked_steps:
        return ToolWorkflowAction(
            action="clarify",
            reason="dependency_blocked",
            notice="工具依赖未满足，相关下游操作未执行；请补充必要信息后重试。",
        )

    followup_requested = planner_need_more_rounds or quality_replan_required
    if followup_requested and round_index >= max_rounds:
        return ToolWorkflowAction(
            action="stop",
            reason="max_rounds_reached",
            notice="工具观察已达到同步对话轮次上限；如需更长的只读任务，请显式发起可恢复工作流。",
        )

    if quality_replan_required:
        return ToolWorkflowAction(
            action="replan",
            reason="tool_result_quality",
            notice="工具结果未达到当前任务的质量要求，正在基于安全反馈重新规划。",
        )

    if aggregate.status == "partial":
        if planner_need_more_rounds:
            return ToolWorkflowAction(
                action="replan",
                reason="partial_result_followup",
            )
        return ToolWorkflowAction(
            action="finalize_partial",
            reason="partial_result_available",
            notice="部分工具已成功返回，回答仅使用通过质量校验的结果；其它分支未纳入回答。",
        )

    if aggregate.status == "succeeded":
        if planner_need_more_rounds:
            return ToolWorkflowAction(action="replan", reason="planner_requested_followup")
        return ToolWorkflowAction(action="stop", reason="completed")

    if aggregate.status in {"failed", "blocked"}:
        if planner_need_more_rounds:
            return ToolWorkflowAction(action="replan", reason="workflow_failure_followup")
        if aggregate.status == "blocked":
            return ToolWorkflowAction(
                action="clarify",
                reason="dependency_blocked",
                notice="工具依赖未满足，相关下游操作未执行；请补充必要信息后重试。",
            )
        return ToolWorkflowAction(
            action="stop",
            reason="all_tool_branches_failed",
            notice="未获得可用于回答的外部工具结果，请检查输入或稍后重试。",
        )

    # ``empty`` 既可能是没有规划工具，也可能来自兼容性 Workflow。只有确实
    # 有证据或错误观察且 Planner 请求续轮时才继续，避免空转消耗五轮预算。
    if planner_need_more_rounds and (has_sources or has_error or quality_replan_required):
        return ToolWorkflowAction(action="replan", reason="planner_requested_followup")
    return ToolWorkflowAction(action="stop", reason="no_evidence_or_error")


def independent_failure_exists(aggregate: ToolWorkflowAggregate) -> bool:
    """判断是否存在可以与其它独立结果并存的失败分支。"""

    return bool(aggregate.independent_failed_steps or aggregate.independent_blocked_steps)


@dataclass
class ToolWorkflowResult:
    sources: list[ExternalSource] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    events: list[ToolTraceEvent] = field(default_factory=list)
    # A deliberately small, sanitized outcome contract for the *next* planning
    # round.  Trace events are useful for audit, but they are too verbose and
    # can contain implementation-specific details to be re-injected wholesale
    # into the Planner prompt.
    feedback: list["ToolWorkflowFeedback"] = field(default_factory=list)
    # Immutable terminal records for every planned synchronous step.  This is
    # the workflow's source of truth; feedback and trace events are projections
    # for separate consumers.
    step_outcomes: list["ToolStepOutcome"] = field(default_factory=list)
    # ``aggregate_status`` 保留给已有调用方兼容读取；新代码应优先读取
    # ``aggregate``，以获得状态之外的安全计数摘要。
    aggregate: ToolWorkflowAggregate = field(default_factory=ToolWorkflowAggregate)
    aggregate_status: str = "empty"
    selected_tool: str = "none"
    error_message: str = ""
    elapsed_ms: int = 0


@dataclass
class ToolStepResult:
    call: PlannedToolCall
    succeeded: bool = False
    execution_status: str = "failed"
    quality_status: str = "not_applicable"
    quality_reasons: list[str] = field(default_factory=list)
    quality_action: str = "block"
    error_category: str = "tool_execution_failed"
    retryable: bool = False
    elapsed_ms: int = 0
    call_fingerprint: str = ""
    sources: list[ExternalSource] = field(default_factory=list)
    # Sources from failed/blocked calls stay available for trace/debugging, but
    # only an explicitly approved result may enter the final answer context.
    expose_sources_to_prompt: bool = False
    notices: list[str] = field(default_factory=list)
    events: list[ToolTraceEvent] = field(default_factory=list)
    error_message: str = ""


EXECUTION_STATUSES = frozenset(
    {
        "pending",
        "ready",
        "running",
        "succeeded",
        "failed",
        "blocked",
        "waiting_approval",
        "cancelled",
        "timed_out",
    }
)
OUTCOME_QUALITY_STATUSES = frozenset({"valid", "uncertain", "invalid", "not_applicable"})
OUTCOME_ACTIONS = frozenset(
    {
        "continue",
        "fallback",
        "replan",
        "clarify",
        "finalize_partial",
        "handoff_durable",
        "stop",
        # Existing deterministic policy uses this name.  Keep it as an
        # internal terminal action while public callers receive `stop`.
        "block",
    }
)


@dataclass(frozen=True)
class ToolStepOutcome:
    """Immutable, sanitized terminal state for one planned synchronous step.

    A Tool result has three independent dimensions: whether the step reached a
    terminal execution state, whether its result has business value, and what
    the code (not the Planner) permits next.  Arguments are deliberately not
    retained here; ``call_fingerprint`` is enough for run-scoped deduplication
    and audit without copying model-controlled or sensitive parameters into
    feedback/trace payloads.
    """

    call_id: str
    tool_key: str
    display_name: str
    execution_status: str
    quality_status: str
    quality_reasons: tuple[str, ...]
    error_category: str
    next_action: str
    depends_on: tuple[str, ...]
    prompt_eligible: bool
    call_fingerprint: str
    elapsed_ms: int
    retryable: bool = False

    def __post_init__(self) -> None:
        if self.execution_status not in EXECUTION_STATUSES:
            raise ValueError(f"Unsupported tool execution status: {self.execution_status}")
        if self.quality_status not in OUTCOME_QUALITY_STATUSES:
            raise ValueError(f"Unsupported tool quality status: {self.quality_status}")
        if self.next_action not in OUTCOME_ACTIONS:
            raise ValueError(f"Unsupported tool next action: {self.next_action}")

    @property
    def unlocks_strict_dependents(self) -> bool:
        return self.execution_status == "succeeded" and self.quality_status == "valid"

    @property
    def is_dependency_blocked(self) -> bool:
        """当前步骤是否因显式上游关系而不能独立执行。"""

        return self.execution_status == "blocked" and bool(self.depends_on)

    @property
    def failure_scope(self) -> str:
        """把独立失败、依赖分支失败和依赖阻断区分开。"""

        if self.is_dependency_blocked:
            return "dependency_blocked"
        if self.depends_on:
            return "dependent_branch"
        return "independent"

    def to_trace_payload(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "tool_key": self.tool_key,
            "execution_status": self.execution_status,
            "quality_status": self.quality_status,
            "quality_reasons": list(self.quality_reasons),
            "error_category": self.error_category,
            "next_action": self.next_action,
            "depends_on": list(self.depends_on),
            "prompt_eligible": self.prompt_eligible,
            "call_fingerprint": self.call_fingerprint,
            "elapsed_ms": self.elapsed_ms,
            "retryable": self.retryable,
        }


@dataclass(frozen=True)
class ToolWorkflowFeedback:
    """Safe execution feedback exposed to a bounded follow-up Planner round.

    This is intentionally not an Adapter error payload or a copy of a Tool
    result.  It tells the Planner only which planned action was unusable, the
    deterministic quality decision, and whether a new plan is appropriate.
    Raw provider errors and untrusted source content stay out of this contract.
    """

    call_id: str
    tool_key: str
    display_name: str
    outcome: str
    quality_status: str
    next_action: str
    reasons: tuple[str, ...] = ()
    depends_on: tuple[str, ...] = ()
    error_category: str = ""
    failure_scope: str = "independent"

    @classmethod
    def from_outcome(cls, outcome: ToolStepOutcome) -> "ToolWorkflowFeedback | None":
        """Project a terminal Outcome into the small Planner-safe contract.

        Successful evidence belongs in the final answer context and does not
        need a failure feedback record.  All other state remains structured;
        no raw provider exception, URL, response body or arguments can cross
        this boundary.
        """

        if outcome.execution_status == "succeeded":
            return None
        return cls(
            call_id=outcome.call_id,
            tool_key=outcome.tool_key,
            display_name=outcome.display_name,
            outcome=outcome.execution_status,
            quality_status=outcome.quality_status,
            next_action=outcome.next_action,
            reasons=outcome.quality_reasons[:4],
            depends_on=outcome.depends_on,
            error_category=outcome.error_category,
            failure_scope=outcome.failure_scope,
        )

    def to_planner_observation(self, *, round_index: int) -> dict[str, Any]:
        readable_reasons = "、".join(self.reasons[:4]) or "未获得可用结果"
        display_text = (
            f"{self.display_name}（{self.tool_key}）执行结果为 {self.outcome}，"
            f"质量状态为 {self.quality_status}；原因：{readable_reasons}；"
            f"失败范围：{ {'dependency_blocked': '严格依赖下游', 'dependent_branch': '有依赖的分支', 'independent': '独立分支'}.get(self.failure_scope, '受控分支') }；"
            f"建议动作：{self.next_action}。"
        )
        return {
            "round": round_index,
            "source_type": "tool_quality_feedback",
            "tool_key": self.tool_key,
            "call_id": self.call_id,
            "outcome": self.outcome,
            "quality_status": self.quality_status,
            "next_action": self.next_action,
            "reasons": list(self.reasons[:4]),
            "depends_on": list(self.depends_on),
            "error_category": self.error_category,
            "failure_scope": self.failure_scope,
            "display_text": display_text,
            "metadata": {},
        }


@dataclass
class ToolRunCallLedger:
    """In-memory ledger for one synchronous Chat tool run.

    It intentionally has no database backing: synchronous Chat is not a
    durable runtime.  The ledger only prevents a later re-plan from executing
    an identical Tool + canonical-arguments pair after a terminal failure that
    the current request cannot safely repair by retrying.  Different arguments
    remain valid re-plans; durable retry/replay uses its own persisted policy.
    """

    _blockers_by_fingerprint: dict[str, ToolStepOutcome] = field(default_factory=dict)

    def blocking_outcome_for(self, *, fingerprint: str) -> ToolStepOutcome | None:
        return self._blockers_by_fingerprint.get(fingerprint)

    def record(self, outcome: ToolStepOutcome) -> None:
        if self._is_reexecution_blocker(outcome):
            self._blockers_by_fingerprint.setdefault(outcome.call_fingerprint, outcome)

    @staticmethod
    def _is_reexecution_blocker(outcome: ToolStepOutcome) -> bool:
        if outcome.execution_status == "waiting_approval":
            return True
        if outcome.execution_status == "failed" and outcome.quality_status in {"invalid", "uncertain"}:
            return True
        if outcome.execution_status == "failed" and not outcome.retryable:
            return True
        # A policy or validation denial cannot become safe merely because the
        # Planner emits the exact same call again. Dependency/cycle/budget
        # blocks are deliberately excluded because a different upstream plan or
        # a later request can make those calls meaningful.
        return outcome.execution_status == "blocked" and outcome.error_category in {
            "policy_blocked",
            "schema_or_scope_blocked",
            "result_binding_invalid",
            "duplicate_across_run",
        }


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

    async def run(
        self,
        *,
        plan: ToolPlan,
        query: str,
        call_ledger: ToolRunCallLedger | None = None,
        max_tool_calls: int | None = None,
        max_parallel_calls: int | None = None,
    ) -> ToolWorkflowResult:
        """Run one bounded ToolPlan and emit immutable terminal outcomes.

        ``call_ledger`` lives for the outer synchronous Chat request and is
        shared across re-plans.  The workflow itself remains fully usable on
        its own, which preserves the existing direct/unit-test call sites.
        """

        started = time.perf_counter()
        result = ToolWorkflowResult(selected_tool=plan.calls[0].category if plan.calls else "none")
        call_ledger = call_ledger or ToolRunCallLedger()
        effective_max_tool_calls = self._effective_max_tool_calls(max_tool_calls)
        effective_max_parallel_calls = self._effective_max_parallel_calls(
            max_parallel_calls=max_parallel_calls,
            max_tool_calls=effective_max_tool_calls,
        )
        calls = plan.calls[:effective_max_tool_calls]
        fallback_call_ids = self._select_fallback_call_ids(
            plan=plan,
            calls=calls,
            query=query,
            max_tool_calls=effective_max_tool_calls,
        )
        terminal_by_call_id: dict[str, ToolStepOutcome] = {}
        sources_by_call_id: dict[str, list[ExternalSource]] = {}
        emitted_states: set[tuple[str, str]] = set()

        def record_state(call: PlannedToolCall, state: str) -> None:
            """Trace non-terminal lifecycle states exactly once per Step."""

            state_key = (call.call_id, state)
            if state_key in emitted_states:
                return
            emitted_states.add(state_key)
            result.events.append(
                ToolTraceEvent(
                    type="tool_workflow_step_state",
                    payload={
                        "workflow": "tool_workflow_v2",
                        "call_id": call.call_id,
                        "tool_key": call.tool_key,
                        "state": state,
                        "depends_on": list(call.depends_on),
                    },
                )
            )

        def record_outcome(outcome: ToolStepOutcome, *, index_for_dependencies: bool = True) -> None:
            result.step_outcomes.append(outcome)
            if index_for_dependencies:
                terminal_by_call_id.setdefault(outcome.call_id, outcome)
            call_ledger.record(outcome)
            feedback = ToolWorkflowFeedback.from_outcome(outcome)
            if feedback:
                result.feedback.append(feedback)
            result.events.append(
                ToolTraceEvent(
                    type="tool_workflow_step_outcome",
                    payload={
                        "workflow": "tool_workflow_v2",
                        **outcome.to_trace_payload(),
                    },
                )
            )

        result.events.append(
            ToolTraceEvent(
                type="tool_workflow_start",
                payload={
                    "workflow": "tool_workflow_v2",
                    "plan_id": plan.plan_id,
                    "planned_calls": len(plan.calls),
                    "max_tool_calls": effective_max_tool_calls,
                    "max_parallel_calls": effective_max_parallel_calls,
                    "executing_calls": len(calls),
                },
            )
        )

        # Calls past the per-plan budget are not silently discarded. They have
        # a terminal Outcome but are intentionally not added to the run ledger:
        # a later, smaller plan may safely include them.
        for call in plan.calls[effective_max_tool_calls:]:
            outcome = self._blocked_outcome(
                call=call,
                error_category="tool_call_budget_exceeded",
                next_action="replan",
                reasons=("tool_call_budget_exceeded",),
            )
            record_outcome(outcome)
            result.events.append(
                ToolTraceEvent(
                    type="tool_workflow_step_skipped",
                    payload={
                        "workflow": "tool_workflow_v2",
                        "call_id": call.call_id,
                        "tool_key": call.tool_key,
                        "reason": "tool_call_budget_exceeded",
                    },
                )
            )

        seen_call_keys: set[tuple[str, str]] = set()
        pending: dict[str, PlannedToolCall] = {}
        for call in calls:
            if call.call_id in pending:
                outcome = self._blocked_outcome(
                    call=call,
                    error_category="duplicate_call_id",
                    next_action="replan",
                    reasons=("duplicate_call_id",),
                )
                record_outcome(outcome, index_for_dependencies=False)
                result.events.append(
                    ToolTraceEvent(
                        type="tool_workflow_step_skipped",
                        payload={
                            "workflow": "tool_workflow_v2",
                            "call_id": call.call_id,
                            "tool_key": call.tool_key,
                            "reason": "duplicate_call_id",
                        },
                    )
                )
                continue
            pending[call.call_id] = call
            record_state(call, "pending")

        step = 0
        while pending:
            ready = [
                call
                for call in pending.values()
                if all(dependency in terminal_by_call_id for dependency in call.depends_on)
            ]
            if not ready:
                for call in list(pending.values()):
                    outcome = self._blocked_outcome(
                        call=call,
                        error_category="unresolved_or_cyclic_dependencies",
                        next_action="stop",
                        reasons=("unresolved_or_cyclic_dependencies",),
                    )
                    record_outcome(outcome)
                    result.events.append(
                        ToolTraceEvent(
                            type="tool_workflow_step_skipped",
                            payload={
                                "workflow": "tool_workflow_v2",
                                "call_id": call.call_id,
                                "tool_key": call.tool_key,
                                "depends_on": call.depends_on,
                                "reason": "unresolved_or_cyclic_dependencies",
                            },
                        )
                    )
                    pending.pop(call.call_id, None)
                break

            for call in ready:
                record_state(call, "ready")

            non_parallel = [call for call in ready if not call.can_parallel]
            if non_parallel:
                ready = [non_parallel[0]]
            else:
                ready = ready[:effective_max_parallel_calls]

            executable: list[PlannedToolCall] = []
            for call in ready:
                dependency_outcomes = [terminal_by_call_id[dependency] for dependency in call.depends_on]
                failed_dependencies = [
                    outcome for outcome in dependency_outcomes if not outcome.unlocks_strict_dependents
                ]
                if failed_dependencies:
                    dependency_quality = {
                        outcome.call_id: outcome.quality_status for outcome in failed_dependencies
                    }
                    quality_blocked = any(
                        outcome.quality_status in {"invalid", "uncertain"}
                        for outcome in failed_dependencies
                    )
                    next_action = (
                        "replan"
                        if any(outcome.next_action in {"replan", "fallback"} for outcome in failed_dependencies)
                        else "block"
                    )
                    error_category = (
                        "dependency_quality_not_usable" if quality_blocked else "dependency_not_succeeded"
                    )
                    outcome = self._blocked_outcome(
                        call=call,
                        error_category=error_category,
                        next_action=next_action,
                        reasons=(error_category,),
                        depends_on=tuple(item.call_id for item in failed_dependencies),
                    )
                    record_outcome(outcome)
                    result.events.append(
                        ToolTraceEvent(
                            type="quality_gate_blocked" if quality_blocked else "tool_workflow_step_skipped",
                            payload={
                                "workflow": "tool_workflow_v2",
                                "call_id": call.call_id,
                                "tool_key": call.tool_key,
                                "depends_on": call.depends_on,
                                "failed_dependencies": [item.call_id for item in failed_dependencies],
                                "dependency_quality": dependency_quality,
                                "dependency_quality_reasons": {
                                    item.call_id: list(item.quality_reasons) for item in failed_dependencies
                                },
                                "next_action": next_action,
                                "reason": error_category,
                            },
                        )
                    )
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
                        outcome = self._blocked_outcome(
                            call=call,
                            error_category="result_binding_invalid",
                            next_action="replan",
                            reasons=("result_binding_invalid",),
                        )
                        record_outcome(outcome)
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
                        pending.pop(call.call_id, None)
                        continue

                call_key = (call.tool_key, self._stable_arguments(call))
                fingerprint = self.call_fingerprint_for(call)
                prior_outcome = call_ledger.blocking_outcome_for(fingerprint=fingerprint)
                if prior_outcome:
                    next_action = "clarify" if prior_outcome.execution_status == "waiting_approval" else "replan"
                    outcome = self._blocked_outcome(
                        call=call,
                        error_category="duplicate_across_run",
                        next_action=next_action,
                        reasons=("duplicate_across_run",),
                    )
                    record_outcome(outcome)
                    result.events.append(
                        ToolTraceEvent(
                            type="tool_workflow_step_skipped",
                            payload={
                                "workflow": "tool_workflow_v2",
                                "call_id": call.call_id,
                                "tool_key": call.tool_key,
                                "reason": "duplicate_across_run",
                                "prior_execution_status": prior_outcome.execution_status,
                                "prior_quality_status": prior_outcome.quality_status,
                            },
                        )
                    )
                    pending.pop(call.call_id, None)
                    continue
                if call_key in seen_call_keys:
                    outcome = self._blocked_outcome(
                        call=call,
                        error_category="duplicate_within_plan",
                        next_action="replan",
                        reasons=("duplicate_tool_call",),
                    )
                    record_outcome(outcome)
                    result.events.append(
                        ToolTraceEvent(
                            type="tool_workflow_step_skipped",
                            payload={
                                "workflow": "tool_workflow_v2",
                                "call_id": call.call_id,
                                "tool_key": call.tool_key,
                                "reason": "duplicate_tool_call",
                            },
                        )
                    )
                    pending.pop(call.call_id, None)
                    continue
                seen_call_keys.add(call_key)
                executable.append(call)

            if not executable:
                continue
            step += 1
            for call in executable:
                record_state(call, "running")
            result.events.append(
                ToolTraceEvent(
                    type="tool_workflow_batch",
                    payload={
                        "workflow": "tool_workflow_v2",
                        "step": step,
                        "mode": "parallel" if len(executable) > 1 else "single",
                        "max_parallel_calls": effective_max_parallel_calls,
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
                        call_fingerprint=self.call_fingerprint_for(call),
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
                                "workflow": "tool_workflow_v2",
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
                sources_by_call_id[step_result.call.call_id] = list(step_result.sources)
                record_outcome(self._outcome_from_step_result(step_result))
                pending.pop(step_result.call.call_id, None)

        result.aggregate = ToolWorkflowAggregate.from_outcomes(result.step_outcomes)
        # 兼容既有 API、诊断字段和测试；阶段 3.1 起完整摘要以
        # ``ToolWorkflowResult.aggregate`` 为唯一计算来源。
        result.aggregate_status = result.aggregate.status
        result.elapsed_ms = int((time.perf_counter() - started) * 1000)
        result.events.append(
            ToolTraceEvent(
                type="tool_workflow_end",
                payload={
                    "workflow": "tool_workflow_v2",
                    "plan_id": plan.plan_id,
                    "status": result.aggregate_status,
                    "elapsed_ms": result.elapsed_ms,
                    "sources_count": len(result.sources),
                    "step_outcomes_count": len(result.step_outcomes),
                    "aggregate": result.aggregate.to_trace_payload(),
                    "error": result.error_message or None,
                },
            )
        )
        return result

    @classmethod
    def _blocked_outcome(
        cls,
        *,
        call: PlannedToolCall,
        error_category: str,
        next_action: str,
        reasons: tuple[str, ...],
        depends_on: tuple[str, ...] | None = None,
    ) -> ToolStepOutcome:
        return ToolStepOutcome(
            call_id=call.call_id,
            tool_key=call.tool_key,
            display_name=call.display_name,
            execution_status="blocked",
            quality_status="not_applicable",
            quality_reasons=reasons,
            error_category=error_category,
            next_action=next_action,
            depends_on=depends_on if depends_on is not None else tuple(call.depends_on),
            prompt_eligible=False,
            call_fingerprint=cls.call_fingerprint_for(call),
            elapsed_ms=0,
            retryable=False,
        )

    @classmethod
    def _outcome_from_step_result(cls, step_result: ToolStepResult) -> ToolStepOutcome:
        return ToolStepOutcome(
            call_id=step_result.call.call_id,
            tool_key=step_result.call.tool_key,
            display_name=step_result.call.display_name,
            execution_status=step_result.execution_status,
            quality_status=step_result.quality_status,
            quality_reasons=tuple(step_result.quality_reasons[:8]),
            error_category=step_result.error_category,
            next_action=step_result.quality_action,
            depends_on=tuple(step_result.call.depends_on),
            prompt_eligible=step_result.expose_sources_to_prompt,
            call_fingerprint=step_result.call_fingerprint or cls.call_fingerprint_for(step_result.call),
            elapsed_ms=max(0, int(step_result.elapsed_ms)),
            retryable=step_result.retryable,
        )

    @staticmethod
    def _aggregate_status(outcomes: list[ToolStepOutcome]) -> str:
        """兼容旧调用方的状态读取，避免复制汇总规则。"""

        return ToolWorkflowAggregate.from_outcomes(outcomes).status

    def _effective_max_tool_calls(self, requested_limit: int | None) -> int:
        """把外层 Budget 限额与 Workflow 自身硬上限取较小值。"""

        if requested_limit is None:
            return self.max_tool_calls
        return max(0, min(self.max_tool_calls, int(requested_limit)))

    @staticmethod
    def _effective_max_parallel_calls(*, max_parallel_calls: int | None, max_tool_calls: int) -> int:
        """并发额度不能超过本轮实际允许执行的调用数。"""

        if max_tool_calls <= 0:
            return 1
        requested = max_tool_calls if max_parallel_calls is None else int(max_parallel_calls)
        return max(1, min(max_tool_calls, requested))

    async def _execute_call(
        self,
        *,
        call: PlannedToolCall,
        query: str,
        plan: ToolPlan,
        allow_fallback: bool,
        call_fingerprint: str,
    ) -> ToolStepResult:
        events = [
            ToolTraceEvent(
                type="tool_workflow_step",
                payload={
                    "workflow": "tool_workflow_v2",
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
            return ToolStepResult(
                call=call,
                execution_status="failed",
                quality_status="not_applicable",
                quality_reasons=["executor_exception"],
                quality_action="replan",
                error_category="executor_exception",
                retryable=True,
                call_fingerprint=call_fingerprint,
                events=events,
                error_message=safe_error,
            )
        events.extend(call_events)
        error_message = call_result.error_message or ""
        quality_status, quality_reasons = self._quality_for_result(call_result)
        result_usable = self._result_is_usable(call_result)
        approval_draft_ready = self._is_approval_draft_ready(
            call_result=call_result,
            quality_status=quality_status,
        )
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
        effective_quality_status = (
            "uncertain"
            if confirmation_required
            else (
                "valid"
                if result_usable
                else ("not_applicable" if call_result.status != "success" else quality_decision.status)
            )
        )
        events.append(
            ToolTraceEvent(
                type="tool_result_quality_decision",
                payload={
                    "workflow": "tool_workflow_v2",
                    "call_id": call.call_id,
                    "tool_key": call.tool_key,
                    "status": quality_decision.status,
                    # 编辑预览本身通过质量校验，只表示 Diff 可供审查；它不是
                    # 已写入的证据，因此下一步必须停在用户确认边界。
                    "action": "clarify" if approval_draft_ready else quality_decision.action,
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
                execution_status="waiting_approval",
                error_category="approval_required",
                retryable=False,
                elapsed_ms=call_result.elapsed_ms,
                call_fingerprint=call_fingerprint,
                notices=[f"{call.display_name}需要用户确认，已跳过执行。"],
                events=events,
                error_message=error_message,
            )

        if approval_draft_ready:
            return ToolStepResult(
                call=call,
                # 预览可进入最终回答，帮助用户确认 Diff；但 ``waiting_approval``
                # 会阻断任何普通依赖或 Result Binding。真正写入仍只能经由
                # workspace.files.apply_edit 的一次性审批 continuation 与 Revision CAS。
                sources=call_result.sources,
                expose_sources_to_prompt=True,
                quality_status="valid",
                quality_reasons=[*quality_reasons, "approval_draft_not_applied"],
                quality_action="clarify",
                execution_status="waiting_approval",
                error_category="approval_draft_ready",
                retryable=False,
                elapsed_ms=call_result.elapsed_ms,
                call_fingerprint=call_fingerprint,
                notices=[f"{call.display_name}已生成编辑预览，尚未写入；请用户确认后再执行修改。"],
                events=events,
                error_message=error_message,
            )

        if result_usable:
            return ToolStepResult(
                call=call,
                succeeded=True,
                execution_status="succeeded",
                quality_status="valid",
                quality_reasons=quality_reasons,
                quality_action="continue",
                error_category="",
                retryable=False,
                elapsed_ms=call_result.elapsed_ms,
                call_fingerprint=call_fingerprint,
                sources=call_result.sources,
                expose_sources_to_prompt=True,
                events=events,
            )

        if call_result.status == "success" and effective_quality_status in {"invalid", "uncertain"}:
            events.append(
                ToolTraceEvent(
                    type="quality_gate_blocked",
                    payload={
                        "workflow": "tool_workflow_v2",
                        "call_id": call.call_id,
                        "tool_key": call.tool_key,
                        "status": effective_quality_status,
                        "reasons": quality_reasons,
                        "next_action": quality_decision.action,
                        "downstream_unlocked": False,
                    },
                )
            )

        if quality_decision.action != "fallback" or not plan.fallback_tool_key or not allow_fallback:
            execution_status = "blocked" if call_result.status == "skipped" else "failed"
            error_category = (
                "schema_or_scope_blocked"
                if execution_status == "blocked"
                else (
                    "quality_result_not_usable"
                    if call_result.status == "success"
                    else "tool_execution_failed"
                )
            )
            return ToolStepResult(
                call=call,
                execution_status=execution_status,
                quality_status=effective_quality_status,
                quality_reasons=quality_reasons,
                quality_action=quality_decision.action,
                error_category=error_category,
                retryable=bool(call_result.retryable) if execution_status == "failed" else False,
                elapsed_ms=call_result.elapsed_ms,
                call_fingerprint=call_fingerprint,
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
                execution_status="failed",
                quality_status="not_applicable",
                quality_reasons=["fallback_executor_exception"],
                quality_action="replan",
                error_category="fallback_executor_exception",
                retryable=True,
                elapsed_ms=call_result.elapsed_ms,
                call_fingerprint=call_fingerprint,
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
        effective_fallback_quality_status = (
            "valid"
            if fallback_usable
            else (
                "not_applicable"
                if fallback_result.status != "success"
                else fallback_decision.status
            )
        )
        events.append(
            ToolTraceEvent(
                type="tool_result_quality_decision",
                payload={
                    "workflow": "tool_workflow_v2",
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
                        "workflow": "tool_workflow_v2",
                        "call_id": call.call_id,
                        "tool_key": call.tool_key,
                        "stage": "fallback_dependency_contract",
                        "status": fallback_dependency_quality.status,
                        "reasons": list(fallback_dependency_quality.reasons),
                        "downstream_unlocked": False,
                    },
                )
            )
        if dependency_contract_satisfied:
            return ToolStepResult(
                call=call,
                succeeded=True,
                execution_status="succeeded",
                quality_status="valid",
                quality_reasons=fallback_quality_reasons,
                quality_action="continue",
                error_category="",
                retryable=False,
                elapsed_ms=call_result.elapsed_ms + fallback_result.elapsed_ms,
                call_fingerprint=call_fingerprint,
                sources=fallback_result.sources,
                expose_sources_to_prompt=True,
                notices=notices,
                events=events,
            )

        fallback_contract_failed = fallback_usable and not dependency_contract_satisfied
        final_quality_status = "invalid" if fallback_contract_failed else effective_fallback_quality_status
        final_quality_reasons = list(fallback_quality_reasons)
        if fallback_contract_failed:
            final_quality_reasons.append("fallback_dependency_contract_not_satisfied")
        return ToolStepResult(
            call=call,
            execution_status="failed",
            quality_status=final_quality_status,
            quality_reasons=final_quality_reasons,
            quality_action="replan" if fallback_contract_failed else fallback_decision.action,
            error_category=(
                "fallback_dependency_contract_not_satisfied"
                if fallback_contract_failed
                else (
                    "fallback_quality_not_usable"
                    if fallback_result.status == "success"
                    else "fallback_execution_failed"
                )
            ),
            retryable=bool(fallback_result.retryable) and not fallback_contract_failed,
            elapsed_ms=call_result.elapsed_ms + fallback_result.elapsed_ms,
            call_fingerprint=call_fingerprint,
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
    def _is_approval_draft_ready(*, call_result: ToolCallResult, quality_status: str) -> bool:
        """识别可展示、但绝不能解锁普通依赖的编辑预览。"""

        return bool(
            call_result.status == "success"
            and quality_status == "valid"
            and str(getattr(call_result, "result_semantics", "")).strip().lower() == "approval_draft"
        )

    @staticmethod
    def _quality_error(call_result: ToolCallResult) -> str:
        return quality_error_for_result(call_result)

    def _select_fallback_call_ids(
        self,
        *,
        plan: ToolPlan,
        calls: list[PlannedToolCall],
        query: str,
        max_tool_calls: int,
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
        remaining_slots = max(0, max_tool_calls - len(calls))
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

    @classmethod
    def call_fingerprint_for(cls, call: PlannedToolCall) -> str:
        """Return a versioned, argument-order-independent call fingerprint.

        The digest, rather than canonical arguments themselves, is written to
        Outcome/Trace records.  This keeps the sync-run dedupe audit useful
        without copying arbitrary model parameters into another observation
        surface.
        """

        canonical = f"tool_call_v1\n{call.tool_key}\n{cls._stable_arguments(call)}"
        return f"sha256:{sha256(canonical.encode('utf-8')).hexdigest()}"
