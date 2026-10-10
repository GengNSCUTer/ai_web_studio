from __future__ import annotations

import asyncio
import time
import unittest
from unittest.mock import patch

from app.services.external_context_service import ExternalContextService
from app.services.tools.catalog import ToolCatalog
from app.services.tools.run_policy import ToolRunBudget
from app.services.tools.schemas import ExternalSource, PlannedToolCall, ToolCallResult, ToolPlan
from app.services.tools.workflow import ToolRunCallLedger, ToolWorkflowService


def make_call(call_id: str, *, depends_on: list[str] | None = None) -> PlannedToolCall:
    return PlannedToolCall(
        call_id=call_id,
        tool_key="web.tavily.search",
        provider="tavily",
        category="web_search",
        display_name=f"测试搜索 {call_id}",
        confidence=1.0,
        reason="验证截止时间和部分结果",
        arguments={"query": call_id},
        depends_on=depends_on or [],
    )


def make_plan(calls: list[PlannedToolCall], *, continue_planning: bool = False) -> ToolPlan:
    return ToolPlan(
        plan_id="deadline-test-plan",
        router="test",
        external_context_allowed=True,
        should_use_tools=bool(calls),
        need_more_rounds=continue_planning,
        calls=calls,
    )


class DeadlineExecutor:
    """慢调用等待取消，快调用输出质量明确的结果，不依赖远程服务速度。"""

    def __init__(self, *, slow: set[str], invalid: set[str] | None = None) -> None:
        self.slow = slow
        self.invalid = invalid or set()
        self.started: list[str] = []
        self.cancelled: list[str] = []
        self.finished: list[str] = []
        self.slow_started = asyncio.Event()
        self.never_finishes = asyncio.Event()

    async def execute(self, call: PlannedToolCall):
        self.started.append(call.call_id)
        if call.call_id in self.slow:
            self.slow_started.set()
            try:
                await self.never_finishes.wait()
            except asyncio.CancelledError:
                self.cancelled.append(call.call_id)
                raise
        self.finished.append(call.call_id)
        return (
            ToolCallResult(
                call=call,
                status="success",
                sources=[
                    ExternalSource(
                        source_type="web_search",
                        provider="tavily",
                        title=f"证据 {call.call_id}",
                        display_text=f"已获取的有效证据 {call.call_id}",
                        metadata={"call_id": call.call_id},
                    )
                ],
                elapsed_ms=1,
                quality_status="invalid" if call.call_id in self.invalid else "valid",
                quality_reasons=["empty_or_irrelevant"] if call.call_id in self.invalid else [],
            ),
            [],
        )


class DeadlinePlanner:
    def __init__(self, plan: ToolPlan, *, wait_on_call: int | None = None, delay: float = 0) -> None:
        self.result = plan
        self.wait_on_call = wait_on_call
        self.delay = delay
        self.calls = 0
        self.cancelled = False
        self.started = asyncio.Event()

    async def plan(self, **kwargs):
        self.calls += 1
        self.started.set()
        try:
            if self.calls == self.wait_on_call:
                await asyncio.Event().wait()
            if self.delay:
                await asyncio.sleep(self.delay)
            return self.result
        except asyncio.CancelledError:
            self.cancelled = True
            raise


def short_remaining_budget(policy):
    """模拟本请求仅剩 80ms，保持真实策略和单调时钟行为。"""

    return ToolRunBudget(
        policy=policy,
        started_at=time.perf_counter() - policy.max_wall_clock_seconds + 0.08,
    )


class ToolDeadlineTest(unittest.IsolatedAsyncioTestCase):
    def workflow(self, executor: DeadlineExecutor) -> ToolWorkflowService:
        return ToolWorkflowService(executor=executor, registry=ToolCatalog())

    async def test_timeout_keeps_fast_evidence_and_counts_slow_attempt(self) -> None:
        executor = DeadlineExecutor(slow={"slow"})
        result = await self.workflow(executor).run(
            plan=make_plan([make_call("fast"), make_call("slow")]),
            query="并发查询",
            deadline=time.perf_counter() + 0.05,
        )
        outcomes = {outcome.call_id: outcome for outcome in result.step_outcomes}
        self.assertTrue(result.deadline_exhausted)
        self.assertEqual(result.aggregate_status, "partial")
        self.assertEqual(result.aggregate.attempted_steps, 2)
        self.assertEqual(result.aggregate.completed_valid_steps, 1)
        self.assertEqual(outcomes["fast"].execution_status, "succeeded")
        self.assertEqual(outcomes["slow"].execution_status, "timed_out")
        self.assertFalse(outcomes["slow"].retryable)
        self.assertEqual([source.title for source in result.sources], ["证据 fast"])
        self.assertEqual(executor.cancelled, ["slow"])

    async def test_timeout_does_not_expose_completed_but_invalid_evidence(self) -> None:
        executor = DeadlineExecutor(slow={"slow"}, invalid={"bad"})
        result = await self.workflow(executor).run(
            plan=make_plan([make_call("bad"), make_call("slow")]),
            query="质量不足",
            deadline=time.perf_counter() + 0.05,
        )
        self.assertEqual(result.sources, [])
        self.assertEqual(result.aggregate.invalid_quality_steps, 1)
        self.assertTrue(result.deadline_exhausted)

    async def test_late_result_after_cancellation_is_not_treated_as_on_time_evidence(self) -> None:
        """客户端吞掉取消并返回晚到结果时，也不能将截止后的输出冒充成功证据。"""

        class LateExecutor(DeadlineExecutor):
            async def execute(self, call):
                try:
                    return await super().execute(call)
                except asyncio.CancelledError:
                    self.slow.clear()
                    return await super().execute(call)

        executor = LateExecutor(slow={"late"})
        result = await self.workflow(executor).run(
            plan=make_plan([make_call("late")]), query="晚到结果",
            deadline=time.perf_counter() + 0.05,
        )
        self.assertEqual(executor.finished, ["late"])
        self.assertEqual(result.sources, [])
        self.assertEqual(result.step_outcomes[0].execution_status, "timed_out")

    async def test_timeout_blocks_dependent_without_counting_it_as_attempted(self) -> None:
        executor = DeadlineExecutor(slow={"upstream"})
        result = await self.workflow(executor).run(
            plan=make_plan([
                make_call("fast"), make_call("upstream"), make_call("child", depends_on=["upstream"]),
            ]),
            query="依赖查询",
            max_parallel_calls=2,
            deadline=time.perf_counter() + 0.05,
        )
        outcomes = {outcome.call_id: outcome for outcome in result.step_outcomes}
        self.assertNotIn("child", executor.started)
        self.assertEqual(outcomes["child"].execution_status, "blocked")
        self.assertEqual(outcomes["child"].error_category, "dependency_not_succeeded")
        self.assertEqual(result.aggregate.total_steps, 3)
        self.assertEqual(result.aggregate.attempted_steps, 2)
        self.assertEqual(result.aggregate.dependency_blocked_steps, 1)

    async def test_expired_deadline_never_starts_a_tool(self) -> None:
        executor = DeadlineExecutor(slow=set())
        result = await self.workflow(executor).run(
            plan=make_plan([make_call("unused")]),
            query="已截止",
            deadline=time.perf_counter() - 1,
        )
        self.assertEqual(executor.started, [])
        self.assertEqual(result.aggregate.attempted_steps, 0)
        self.assertEqual(result.aggregate.blocked_steps, 1)

    async def test_outer_cancellation_propagates_and_cleans_child_tasks(self) -> None:
        executor = DeadlineExecutor(slow={"slow-a", "slow-b"})
        task = asyncio.create_task(self.workflow(executor).run(
            plan=make_plan([make_call("slow-a"), make_call("slow-b")]), query="主动取消",
        ))
        await executor.slow_started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertCountEqual(executor.cancelled, ["slow-a", "slow-b"])
        self.assertEqual(executor.finished, [])

    async def test_timed_out_call_cannot_be_silently_reissued_in_same_run(self) -> None:
        executor = DeadlineExecutor(slow={"slow"})
        workflow = self.workflow(executor)
        ledger = ToolRunCallLedger()
        first = await workflow.run(
            plan=make_plan([make_call("slow")]), query="第一次",
            call_ledger=ledger, deadline=time.perf_counter() + 0.05,
        )
        executor.slow.clear()
        second = await workflow.run(
            plan=make_plan([make_call("slow")]), query="不能隐式重发", call_ledger=ledger,
        )
        self.assertEqual(first.step_outcomes[0].execution_status, "timed_out")
        self.assertEqual(second.step_outcomes[0].error_category, "duplicate_across_run")
        self.assertEqual(executor.started, ["slow"])

    async def test_external_timeout_keeps_sources_and_tells_final_model_about_partial_work(self) -> None:
        executor = DeadlineExecutor(slow={"slow"})
        planner = DeadlinePlanner(make_plan([make_call("fast"), make_call("slow")], continue_planning=True))
        service = ExternalContextService(planner=planner, workflow=self.workflow(executor))
        with patch("app.services.external_context_service.ToolRunBudget", side_effect=short_remaining_budget):
            result = await service.build_context(query="汇总两路查询", enabled=True, max_chars=2000)
        self.assertEqual(result.diagnostics["external_tool_next_action"], "finalize_partial")
        self.assertEqual(result.diagnostics["external_agent_terminal_reason"], "tool_wall_clock_budget_exhausted")
        self.assertEqual(result.diagnostics["external_tool_run_budget"]["tool_calls_used"], 2)
        self.assertEqual(result.diagnostics["external_tool_workflow_aggregate_status"], "partial")
        self.assertIn("证据 fast", result.context_text)
        self.assertIn("未完成", result.context_text)
        self.assertNotIn("证据 slow", result.context_text)
        self.assertEqual(planner.calls, 1)
        self.assertEqual(sum(event.type == "tool_agent_terminal" for event in result.tool_events), 1)

    async def test_first_planner_timeout_has_diagnostics_without_a_fabricated_plan(self) -> None:
        executor = DeadlineExecutor(slow=set())
        planner = DeadlinePlanner(make_plan([]), wait_on_call=1)
        service = ExternalContextService(planner=planner, workflow=self.workflow(executor))
        started = time.perf_counter()
        with patch("app.services.external_context_service.ToolRunBudget", side_effect=short_remaining_budget):
            result = await service.build_context(query="规划超时", enabled=True, max_chars=2000)
        self.assertLess(time.perf_counter() - started, 0.8)
        self.assertTrue(planner.cancelled)
        self.assertIsNone(result.tool_plan)
        self.assertEqual(executor.started, [])
        self.assertEqual(result.diagnostics["external_context_enabled"], 1)
        self.assertEqual(result.diagnostics["external_context_error"], 1)
        self.assertEqual(result.diagnostics["external_tool_run_budget"]["tool_calls_used"], 0)
        self.assertGreaterEqual(result.diagnostics["external_planning_latency_ms"], 50)
        self.assertEqual(result.diagnostics["external_execution_latency_ms"], 0)
        self.assertTrue(result.notices)
        self.assertIn("未完成", result.context_text)
        timeout = next(event for event in result.tool_events if event.type == "tool_agent_budget_timeout")
        self.assertEqual(timeout.payload["stage"], "planner")

    async def test_second_planner_timeout_preserves_previous_round_evidence(self) -> None:
        executor = DeadlineExecutor(slow=set())
        planner = DeadlinePlanner(make_plan([make_call("fast")], continue_planning=True), wait_on_call=2)
        service = ExternalContextService(planner=planner, workflow=self.workflow(executor))
        with patch("app.services.external_context_service.ToolRunBudget", side_effect=short_remaining_budget):
            result = await service.build_context(query="继续查询", enabled=True, max_chars=2000)
        self.assertEqual(planner.calls, 2)
        self.assertTrue(planner.cancelled)
        self.assertEqual(executor.started, ["fast"])
        self.assertEqual(len(result.sources), 1)
        self.assertEqual(result.diagnostics["external_tool_next_action"], "finalize_partial")

    async def test_normal_no_tool_planning_latency_is_not_reported_as_zero(self) -> None:
        planner = DeadlinePlanner(make_plan([]), delay=0.02)
        result = await ExternalContextService(planner=planner).build_context(
            query="普通问题", enabled=True, max_chars=2000,
        )
        self.assertEqual(result.diagnostics["external_agent_terminal_reason"], "no_tool_needed")
        self.assertGreaterEqual(result.diagnostics["external_planning_latency_ms"], 15)
        self.assertGreaterEqual(result.diagnostics["external_context_latency_ms"], 15)
        self.assertEqual(result.diagnostics["external_execution_latency_ms"], 0)

    async def test_unavailable_planning_without_safe_fallback_is_not_no_tool_needed(self) -> None:
        plan = make_plan([])
        plan.trace_events = [
            {"type": "tool_candidate_selection", "candidates": [{"tool_key": "workspace.files.read"}]},
            {"type": "tool_fallback", "reason": "规划模型超时。"},
        ]
        executor = DeadlineExecutor(slow=set())
        result = await ExternalContextService(planner=DeadlinePlanner(plan), workflow=self.workflow(executor)).build_context(
            query="它的第81行原文是什么？别联网", enabled=True, max_chars=2000,
        )
        self.assertEqual(result.diagnostics["external_agent_terminal_reason"], "tool_planning_unavailable")
        self.assertEqual(result.diagnostics["external_context_error"], 1)
        self.assertEqual(executor.started, [])
        self.assertIn("未取得", result.context_text)
        self.assertIn("不得", result.context_text)

    async def test_explicit_empty_candidates_are_not_a_planning_error(self) -> None:
        plan = make_plan([])
        plan.trace_events = [
            {"type": "tool_candidate_selection", "candidates": []},
            {"type": "tool_fallback", "reason": "缺少运行时。"},
        ]
        result = await ExternalContextService(planner=DeadlinePlanner(plan)).build_context(
            query="不要调用任何工具", enabled=True, max_chars=2000,
        )
        self.assertEqual(result.diagnostics["external_agent_terminal_reason"], "no_tool_needed")
        self.assertEqual(result.diagnostics["external_context_error"], 0)
        self.assertIsNone(result.context_text)

    async def test_planner_cancellation_is_not_converted_to_budget_timeout(self) -> None:
        planner = DeadlinePlanner(make_plan([]), wait_on_call=1)
        task = asyncio.create_task(ExternalContextService(planner=planner).build_context(
            query="取消规划", enabled=True, max_chars=2000,
        ))
        await planner.started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(planner.cancelled)


if __name__ == "__main__":
    unittest.main()
