"""迁移对照：真实StateGraph控制续轮，原业务执行器仍负责预算和安全。"""

import asyncio
from copy import deepcopy
import unittest
from unittest.mock import patch

from app.services.agent_graph.sync_tool_graph import build_sync_tool_graph
from app.services.external_context_service import ExternalContextService
from app.services.tools.catalog import ToolCatalog
from app.services.tools.run_policy import resolve_tool_run_policy
from app.services.tools.workflow import ToolWorkflowService
from backend.tests.test_tool_deadline import DeadlineExecutor, DeadlinePlanner, make_call, make_plan, short_remaining_budget


class SequencePlanner:
    def __init__(self, plans):
        self.plans = plans
        self.observations = []
        self.calls = 0

    async def plan(self, **kwargs):
        self.observations.append(deepcopy(kwargs["observations"]))
        plan = self.plans[min(self.calls, len(self.plans)-1)]
        self.calls += 1
        return deepcopy(plan)


class SyncToolGraphTest(unittest.IsolatedAsyncioTestCase):
    def service(self, *, engine="langgraph", planner=None, executor=None, policy=None):
        executor = executor or DeadlineExecutor(slow=set())
        planner = planner or DeadlinePlanner(make_plan([make_call("one")]))
        return ExternalContextService(planner=planner,
            workflow=ToolWorkflowService(executor=executor, registry=ToolCatalog()),
            orchestration=engine, run_policy=policy), executor, planner

    async def run_service(self, service, query="汇总测试查询"):
        return await service.build_context(query=query, enabled=True, max_chars=4000)

    def test_graph_has_distinct_nodes_and_conditional_loop(self):
        graph = build_sync_tool_graph().get_graph()
        self.assertTrue({"plan", "execute", "observe", "finalize"}.issubset(graph.nodes))
        edges = {(edge.source, edge.target) for edge in graph.edges}
        self.assertIn(("observe", "plan"), edges)
        self.assertIn(("plan", "finalize"), edges)
        self.assertIn(("execute", "observe"), edges)
        self.assertIsNone(build_sync_tool_graph().checkpointer)

    async def test_no_tool_skips_execution_and_finishes_once(self):
        service, executor, planner = self.service(planner=DeadlinePlanner(make_plan([])))
        result = await self.run_service(service)
        self.assertEqual(executor.started, [])
        nodes = [e.payload["node"] for e in result.tool_events if e.type == "tool_graph_node"]
        self.assertEqual(nodes, ["plan", "finalize"])
        self.assertEqual(result.diagnostics["external_orchestrator"], "langgraph")

    async def test_two_rounds_pass_observations_and_scope_duplicate_ids(self):
        first = make_plan([make_call("same")], continue_planning=True)
        second = make_plan([make_call("same")])
        second.calls[0].arguments = {"query": "different evidence"}
        planner = SequencePlanner([first, second])
        service, executor, _ = self.service(planner=planner)
        result = await self.run_service(service)
        self.assertEqual(planner.calls, 2)
        self.assertTrue(planner.observations[1])
        self.assertEqual(len(set(executor.started)), 2)
        nodes = [e.payload["node"] for e in result.tool_events if e.type == "tool_graph_node"]
        self.assertEqual(nodes, ["plan", "execute", "observe", "plan", "execute", "observe", "finalize"])

    async def test_legacy_and_graph_keep_same_result_and_business_events(self):
        results = []
        for engine in ("legacy", "langgraph"):
            executor = DeadlineExecutor(slow=set(), invalid={"bad"})
            service, _, _ = self.service(engine=engine, executor=executor,
                planner=DeadlinePlanner(make_plan([make_call("good"), make_call("bad")])))
            results.append(await self.run_service(service))
        old, graph = results
        self.assertEqual(old.context_text, graph.context_text)
        self.assertEqual(old.notices, graph.notices)
        self.assertEqual(old.details["tool_workflow_aggregate"], graph.details["tool_workflow_aggregate"])
        for key in ("external_agent_terminal_reason", "external_tool_next_action", "external_context_error"):
            self.assertEqual(old.diagnostics[key], graph.diagnostics[key])
        def events(result):
            return [e.type for e in result.tool_events if e.type != "tool_graph_node"]
        self.assertEqual(events(old), events(graph))

    async def test_eight_round_budget_is_not_graph_recursion_limit(self):
        policy = resolve_tool_run_policy("guided_research")
        plans = [make_plan([make_call(str(i))], continue_planning=True) for i in range(8)]
        planner = SequencePlanner(plans)
        service, executor, _ = self.service(planner=planner, policy=policy)
        result = await self.run_service(service)
        self.assertEqual(planner.calls, 8)
        self.assertEqual(len(executor.started), 8)
        self.assertEqual(result.diagnostics["external_tool_run_budget"]["planning_rounds_used"], 8)

    async def test_zero_remaining_budget_never_runs_planner(self):
        service, executor, planner = self.service()
        def exhausted(policy):
            budget = short_remaining_budget(policy)
            budget.started_at -= 1
            return budget
        with patch("app.services.external_context_service.ToolRunBudget", side_effect=exhausted):
            result = await self.run_service(service)
        self.assertEqual(planner.calls, 0)
        self.assertFalse(executor.started)
        self.assertEqual(result.diagnostics["external_agent_terminal_reason"], "tool_wall_clock_budget_exhausted")

    async def test_cancel_does_not_execute_legacy_fallback(self):
        planner = DeadlinePlanner(make_plan([make_call("one")]), wait_on_call=1)
        service, executor, _ = self.service(planner=planner)
        with patch("app.services.tools.loop_runtime.SyncToolRun.run_legacy", side_effect=AssertionError("不允许失败后重跑")):
            task = asyncio.create_task(self.run_service(service))
            await planner.started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertFalse(executor.started)
        self.assertTrue(planner.cancelled)

    async def test_unexpected_exception_is_not_hidden_by_engine_fallback(self):
        class BrokenPlanner:
            async def plan(self, **kwargs):
                raise RuntimeError("受控规划错误")
        service, executor, _ = self.service(planner=BrokenPlanner())
        with patch("app.services.tools.loop_runtime.SyncToolRun.run_legacy", side_effect=AssertionError("不允许失败后重跑")):
            with self.assertRaisesRegex(RuntimeError, "受控规划错误"):
                await self.run_service(service)
        self.assertFalse(executor.started)

    async def test_separate_requests_do_not_share_budget_or_sources(self):
        left, _, _ = self.service(planner=DeadlinePlanner(make_plan([make_call("left")])) )
        right, _, _ = self.service(planner=DeadlinePlanner(make_plan([make_call("right")])) )
        a, b = await asyncio.gather(self.run_service(left), self.run_service(right))
        self.assertIn("left", a.context_text)
        self.assertNotIn("right", a.context_text)
        self.assertIn("right", b.context_text)
        self.assertEqual(a.diagnostics["external_tool_run_budget"]["tool_calls_used"], 1)
        self.assertEqual(b.diagnostics["external_tool_run_budget"]["tool_calls_used"], 1)

    async def test_host_trace_setting_does_not_enable_external_graph_tracing(self):
        from langsmith import utils
        class CheckingPlanner:
            async def plan(self, **kwargs):
                self.enabled = utils.tracing_is_enabled()
                return make_plan([])
        planner = CheckingPlanner()
        service, _, _ = self.service(planner=planner)
        with patch.dict("os.environ", {"LANGSMITH_TRACING": "true"}):
            await self.run_service(service)
        self.assertFalse(planner.enabled)

    def test_invalid_engine_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "编排引擎"):
            self.service(engine="unknown")


if __name__ == "__main__":
    unittest.main()
