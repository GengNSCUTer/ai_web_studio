from __future__ import annotations

from dataclasses import dataclass, field
import time


TOOL_RUN_MODES = frozenset(
    {
        "quick_chat",
        "guided_research",
        "workspace_review",
        "edit_proposal",
        "durable_task",
    }
)


@dataclass(frozen=True)
class ToolRunPolicy:
    """代码侧定义的一种工具运行模式。

    Planner 只能在这些固定上限内产生计划，不能通过 Prompt、Tool 返回或自身
    JSON 扩大轮次、调用次数、并发度或时长。``durable_task`` 仅作为后续显式
    handoff 的目标模式，不能直接在同步 Chat 中运行。
    """

    mode: str
    max_planning_rounds: int
    max_total_tool_calls: int
    max_calls_per_plan: int
    max_parallel_calls: int
    max_replans: int
    max_wall_clock_seconds: int
    max_evidence_chars: int
    synchronous: bool = True

    def __post_init__(self) -> None:
        if self.mode not in TOOL_RUN_MODES:
            raise ValueError(f"Unsupported tool run mode: {self.mode}")
        values = {
            "max_planning_rounds": self.max_planning_rounds,
            "max_total_tool_calls": self.max_total_tool_calls,
            "max_calls_per_plan": self.max_calls_per_plan,
            "max_parallel_calls": self.max_parallel_calls,
            "max_replans": self.max_replans,
            "max_wall_clock_seconds": self.max_wall_clock_seconds,
            "max_evidence_chars": self.max_evidence_chars,
        }
        invalid = [name for name, value in values.items() if not isinstance(value, int) or value < 1]
        if invalid:
            raise ValueError(f"Invalid tool run policy fields: {', '.join(invalid)}")
        if self.max_calls_per_plan > self.max_total_tool_calls:
            raise ValueError("max_calls_per_plan cannot exceed max_total_tool_calls")
        if self.max_parallel_calls > self.max_calls_per_plan:
            raise ValueError("max_parallel_calls cannot exceed max_calls_per_plan")

    def to_public_dict(self) -> dict[str, int | str | bool]:
        """返回安全的预算配置，不包含用户输入、工具参数或 Provider 信息。"""

        return {
            "mode": self.mode,
            "max_planning_rounds": self.max_planning_rounds,
            "max_total_tool_calls": self.max_total_tool_calls,
            "max_calls_per_plan": self.max_calls_per_plan,
            "max_parallel_calls": self.max_parallel_calls,
            "max_replans": self.max_replans,
            "max_wall_clock_seconds": self.max_wall_clock_seconds,
            "max_evidence_chars": self.max_evidence_chars,
            "synchronous": self.synchronous,
        }


# 这些常量是当前产品的明确能力边界，不从模型输出、请求体或数据库自由读取。
# quick_chat 保持既有五轮口径；更长的研究链路必须由用户显式选择 guided_research。
TOOL_RUN_POLICIES: dict[str, ToolRunPolicy] = {
    "quick_chat": ToolRunPolicy(
        mode="quick_chat",
        max_planning_rounds=5,
        max_total_tool_calls=10,
        max_calls_per_plan=3,
        max_parallel_calls=2,
        # 首轮之外最多四次续轮，保持历史 quick_chat 的五轮兼容口径。
        max_replans=4,
        max_wall_clock_seconds=35,
        max_evidence_chars=6000,
    ),
    "guided_research": ToolRunPolicy(
        mode="guided_research",
        max_planning_rounds=8,
        max_total_tool_calls=16,
        max_calls_per_plan=3,
        max_parallel_calls=3,
        max_replans=7,
        max_wall_clock_seconds=75,
        max_evidence_chars=8000,
    ),
    "workspace_review": ToolRunPolicy(
        mode="workspace_review",
        max_planning_rounds=6,
        max_total_tool_calls=12,
        max_calls_per_plan=3,
        max_parallel_calls=2,
        max_replans=5,
        max_wall_clock_seconds=50,
        max_evidence_chars=6000,
    ),
    "edit_proposal": ToolRunPolicy(
        mode="edit_proposal",
        max_planning_rounds=4,
        max_total_tool_calls=6,
        max_calls_per_plan=2,
        max_parallel_calls=1,
        max_replans=3,
        max_wall_clock_seconds=35,
        max_evidence_chars=4000,
    ),
    "durable_task": ToolRunPolicy(
        mode="durable_task",
        max_planning_rounds=1,
        max_total_tool_calls=20,
        max_calls_per_plan=20,
        max_parallel_calls=4,
        max_replans=1,
        max_wall_clock_seconds=30,
        max_evidence_chars=8000,
        synchronous=False,
    ),
}


def resolve_tool_run_policy(mode: str | None = None) -> ToolRunPolicy:
    """解析固定运行模式；缺省保持既有同步 Chat 的 quick_chat 口径。"""

    normalized = str(mode or "quick_chat").strip().lower() or "quick_chat"
    try:
        return TOOL_RUN_POLICIES[normalized]
    except KeyError as exc:
        raise ValueError(f"Unsupported tool run mode: {normalized}") from exc


@dataclass
class ToolRunBudget:
    """单次同步请求的内存态预算账本。

    它不替代 Durable Run 的数据库 Checkpoint，也不提供跨请求恢复；请求结束后
    即释放。计数只反映已经真正进入执行阶段的调用，预算截断产生的 blocked
    Outcome 不会重复扣减额度。
    """

    policy: ToolRunPolicy
    started_at: float = field(default_factory=time.perf_counter)
    planning_rounds_used: int = 0
    tool_calls_used: int = 0
    replans_used: int = 0

    @property
    def remaining_tool_calls(self) -> int:
        return max(0, self.policy.max_total_tool_calls - self.tool_calls_used)

    @property
    def remaining_replans(self) -> int:
        return max(0, self.policy.max_replans - self.replans_used)

    @property
    def elapsed_ms(self) -> int:
        return max(0, int((time.perf_counter() - self.started_at) * 1000))

    @property
    def remaining_wall_clock_ms(self) -> int:
        return max(0, self.policy.max_wall_clock_seconds * 1000 - self.elapsed_ms)

    def terminal_limit_reason(self) -> str | None:
        if self.elapsed_ms >= self.policy.max_wall_clock_seconds * 1000:
            return "tool_wall_clock_budget_exhausted"
        if self.planning_rounds_used >= self.policy.max_planning_rounds:
            return "tool_round_budget_exhausted"
        if self.remaining_tool_calls <= 0:
            return "tool_call_budget_exhausted"
        return None

    def begin_round(self) -> None:
        reason = self.terminal_limit_reason()
        if reason:
            raise ValueError(reason)
        self.planning_rounds_used += 1

    def record_attempted_tool_calls(self, count: int) -> None:
        normalized = max(0, int(count))
        self.tool_calls_used = min(
            self.policy.max_total_tool_calls,
            self.tool_calls_used + normalized,
        )

    def can_replan(self) -> bool:
        return bool(
            self.replans_used < self.policy.max_replans
            and self.remaining_tool_calls > 0
            and self.remaining_wall_clock_ms > 0
            and self.planning_rounds_used < self.policy.max_planning_rounds
        )

    def consume_replan(self) -> None:
        if not self.can_replan():
            raise ValueError(self.replan_limit_reason() or "tool_replan_budget_exhausted")
        self.replans_used += 1

    def replan_limit_reason(self) -> str | None:
        if self.remaining_wall_clock_ms <= 0:
            return "tool_wall_clock_budget_exhausted"
        if self.planning_rounds_used >= self.policy.max_planning_rounds:
            return "tool_round_budget_exhausted"
        if self.remaining_tool_calls <= 0:
            return "tool_call_budget_exhausted"
        if self.replans_used >= self.policy.max_replans:
            return "tool_replan_budget_exhausted"
        return None

    def to_trace_payload(self) -> dict[str, int | str | bool]:
        return {
            "mode": self.policy.mode,
            "planning_rounds_used": self.planning_rounds_used,
            "max_planning_rounds": self.policy.max_planning_rounds,
            "tool_calls_used": self.tool_calls_used,
            "max_total_tool_calls": self.policy.max_total_tool_calls,
            "remaining_tool_calls": self.remaining_tool_calls,
            "replans_used": self.replans_used,
            "max_replans": self.policy.max_replans,
            "remaining_replans": self.remaining_replans,
            "max_calls_per_plan": self.policy.max_calls_per_plan,
            "max_parallel_calls": self.policy.max_parallel_calls,
            "elapsed_ms": self.elapsed_ms,
            "remaining_wall_clock_ms": self.remaining_wall_clock_ms,
            "max_wall_clock_seconds": self.policy.max_wall_clock_seconds,
            "max_evidence_chars": self.policy.max_evidence_chars,
            "synchronous": self.policy.synchronous,
        }
