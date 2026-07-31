from __future__ import annotations

import unittest

from app.services.external_context_service import ExternalContextService
from app.services.skill_catalog import SkillExecutionContext
from app.services.tools.planner import DeterministicToolPlanner
from app.services.tools.schemas import ExternalSource, PlannedToolCall, ToolCallResult, ToolPlan, ToolTraceEvent
from app.services.tools.workflow import ToolWorkflowResult


class FakeExecutor:
    def __init__(self) -> None:
        self.calls = []

    async def execute(self, call):
        self.calls.append(call)
        return (
            ToolCallResult(call=call, status="success", sources=[], elapsed_ms=1),
            [
                ToolTraceEvent(
                    type="tool_call_end",
                    payload={
                        "call_id": call.call_id,
                        "tool_key": call.tool_key,
                        "provider": call.provider,
                        "category": call.category,
                        "display_name": call.display_name,
                        "status": "success",
                        "elapsed_ms": 1,
                        "sources_count": 0,
                    },
                )
            ],
        )


class FakeLoopPlanner:
    def __init__(self) -> None:
        self.observations_seen = []
        self.calls = 0

    async def plan(self, *, query, enabled, runtime, recent_messages=None, observations=None):
        self.calls += 1
        self.observations_seen.append(list(observations or []))
        if self.calls == 1:
            return ToolPlan(
                plan_id="loop-plan-1",
                router="fake",
                external_context_allowed=True,
                should_use_tools=True,
                need_more_rounds=True,
                calls=[
                    PlannedToolCall(
                        call_id="route",
                        tool_key="amap.maps.direction.driving",
                        provider="amap",
                        category="map_route",
                        display_name="高德路线",
                        confidence=0.9,
                        reason="route",
                        arguments={"origin": "深圳", "destination": "汕头"},
                    )
                ],
            )
        return ToolPlan(
            plan_id="loop-plan-2",
            router="fake",
            external_context_allowed=True,
            should_use_tools=False,
            calls=[],
        )


class AlwaysContinuePlanner:
    async def plan(self, *, query, enabled, runtime, recent_messages=None, observations=None):
        return ToolPlan(
            plan_id=f"continue-{len(observations or [])}",
            router="fake",
            external_context_allowed=True,
            should_use_tools=True,
            need_more_rounds=True,
            calls=[
                PlannedToolCall(
                    call_id=f"route-{len(observations or [])}",
                    tool_key="amap.maps.direction.driving",
                    provider="amap",
                    category="map_route",
                    display_name="高德路线",
                    confidence=0.9,
                    reason="route",
                    arguments={"origin": "深圳", "destination": "汕头"},
                )
            ],
        )


class FakeLoopWorkflow:
    async def run(self, *, plan, query):
        return ToolWorkflowResult(
            sources=[
                ExternalSource(
                    source_type="map_route",
                    provider="amap",
                    title="深圳到汕头路线",
                    display_text="预计耗时 3 小时 44 分钟。",
                )
            ],
            selected_tool="map_route",
            elapsed_ms=5,
            events=[ToolTraceEvent(type="tool_workflow_end", payload={"sources_count": 1})],
        )


class FakeErrorFeedbackWorkflow:
    async def run(self, *, plan, query):
        return ToolWorkflowResult(
            sources=[],
            selected_tool="workspace_file",
            error_message="old_string 出现 2 次，请提供更多上下文。",
            elapsed_ms=2,
            events=[ToolTraceEvent(type="tool_workflow_end", payload={"sources_count": 0})],
        )


class ToolRouterTest(unittest.TestCase):
    def test_explicit_skill_is_traced_through_planner_and_result(self) -> None:
        async def run_test() -> None:
            skill = SkillExecutionContext(
                skill_key="workspace.document-review",
                version="1.1.0",
                display_name="项目文档审阅",
                description="review",
                planner_instructions=("只读审阅。",),
                output_contract=("给出来源。",),
                allowed_tool_keys=(
                    "workspace.files.list",
                    "workspace.files.search",
                    "workspace.files.read",
                ),
                required_tool_keys=(
                    "workspace.files.list",
                    "workspace.files.search",
                    "workspace.files.read",
                ),
                optional_tool_keys=(),
                requires_tool_execution=True,
            )
            service = ExternalContextService(workflow=FakeLoopWorkflow())
            result = await service.build_context(
                query="审阅当前工作区文档",
                enabled=True,
                max_chars=2000,
                recent_messages=[],
                skill_context=skill,
            )

            event_types = [event.type for event in result.tool_events]
            self.assertEqual(event_types[0], "skill_activation")
            self.assertIn("skill_result", event_types)
            self.assertEqual(result.details["active_skill"]["skill_key"], skill.skill_key)
            self.assertEqual(result.diagnostics["skill_active"], 1)
            self.assertTrue(
                {call.tool_key for call in result.tool_plan.calls}.issubset(set(skill.allowed_tool_keys))
            )

        import asyncio

        asyncio.run(run_test())

    def test_distance_queries_route_to_amap_map(self) -> None:
        router = DeterministicToolPlanner()
        queries = [
            "深圳松岗离汕头市潮阳区西凤村多远",
            "深圳松岗和汕头市潮阳区西凤村相距多少公里",
        ]

        for query in queries:
            with self.subTest(query=query):
                plan = router.plan(query=query, enabled=True)

                self.assertTrue(plan.should_use_tools)
                self.assertEqual(len(plan.calls), 1)
                self.assertEqual(plan.calls[0].tool_key, "amap.maps.distance")
                self.assertEqual(plan.calls[0].category, "map_distance")

    def test_route_duration_queries_route_to_amap_route(self) -> None:
        router = DeterministicToolPlanner()
        plan = router.plan(query="深圳松岗到汕头市潮阳区西凤村开车多久", enabled=True)

        self.assertTrue(plan.should_use_tools)
        self.assertEqual(plan.calls[0].tool_key, "amap.maps.direction.driving")
        self.assertEqual(plan.calls[0].category, "map_route")

    def test_multi_origin_distance_query_splits_origins(self) -> None:
        router = DeterministicToolPlanner()

        plan = router.plan(query="深圳松岗和广州南站分别离汕头市潮阳区西凤村多远，哪个近一点？", enabled=True)

        self.assertTrue(plan.should_use_tools)
        self.assertEqual(plan.calls[0].tool_key, "amap.maps.distance")
        self.assertEqual(plan.calls[0].arguments["origins"], ["深圳松岗", "广州南站"])
        self.assertEqual(plan.calls[0].arguments["destination"], "汕头市潮阳区西凤村")

    def test_external_context_disabled_does_not_require_workflow_result(self) -> None:
        async def run_test() -> None:
            service = ExternalContextService()

            result = await service.build_context(
                query="你好，简单介绍一下你自己",
                enabled=False,
                max_chars=2000,
                recent_messages=[],
            )

            self.assertIsNone(result.context_text)
            self.assertEqual(result.sources, [])
            self.assertEqual(result.diagnostics["external_context_enabled"], 0)
            self.assertEqual(result.diagnostics["external_tool_called"], "none")
            self.assertFalse(result.diagnostics["external_context_error"])
            self.assertEqual(result.diagnostics["external_tool_events_total"], 0)
            self.assertEqual(result.tool_events, [])
            self.assertIsNone(result.tool_plan)

        import asyncio

        asyncio.run(run_test())

    def test_external_context_rewrites_coreference_before_routing(self) -> None:
        async def run_test() -> None:
            executor = FakeExecutor()
            service = ExternalContextService(executor=executor)

            result = await service.build_context(
                query="他们离汕头市潮阳区西凤村多远",
                enabled=True,
                max_chars=2000,
                recent_messages=[
                    {"role": "user", "content": "深圳松岗和广州南站这两个地点。"},
                    {"role": "assistant", "content": "深圳松岗位于宝安区，广州南站位于番禺区。"},
                ],
            )

            self.assertEqual(executor.calls[0].tool_key, "amap.maps.distance")
            self.assertIn("深圳松岗", executor.calls[0].arguments["origins"])
            self.assertIn("汕头市潮阳区西凤村", executor.calls[0].arguments["destination"])
            self.assertEqual(result.tool_plan.original_query, "他们离汕头市潮阳区西凤村多远")
            self.assertTrue(result.tool_plan.rewritten_query)
            self.assertTrue(any(event.type == "tool_query_rewrite" for event in result.tool_events))

        import asyncio

        asyncio.run(run_test())

    def test_external_context_can_replan_with_observations(self) -> None:
        async def run_test() -> None:
            planner = FakeLoopPlanner()
            service = ExternalContextService(planner=planner, workflow=FakeLoopWorkflow())

            result = await service.build_context(
                query="深圳到汕头路上有哪些服务区",
                enabled=True,
                max_chars=2000,
                recent_messages=[],
            )

            self.assertEqual(planner.calls, 2)
            self.assertEqual(planner.observations_seen[0], [])
            self.assertTrue(planner.observations_seen[1])
            self.assertIn("预计耗时", planner.observations_seen[1][0]["display_text"])
            self.assertEqual(len(result.sources), 1)
            event_types = [event.type for event in result.tool_events]
            self.assertIn("tool_agent_round_start", event_types)
            self.assertIn("tool_agent_round_end", event_types)

        import asyncio

        asyncio.run(run_test())

    def test_external_context_can_replan_from_sanitized_tool_error(self) -> None:
        async def run_test() -> None:
            planner = FakeLoopPlanner()
            service = ExternalContextService(planner=planner, workflow=FakeErrorFeedbackWorkflow())

            result = await service.build_context(
                query="修改项目文件",
                enabled=True,
                max_chars=2000,
                recent_messages=[],
            )

            self.assertEqual(planner.calls, 2)
            self.assertEqual(planner.observations_seen[1][0]["source_type"], "tool_error_feedback")
            self.assertIn("出现 2 次", planner.observations_seen[1][0]["display_text"])
            self.assertTrue(result.diagnostics["external_context_error"])

        import asyncio

        asyncio.run(run_test())

    def test_second_round_continuation_is_observable_but_not_executed(self) -> None:
        async def run_test() -> None:
            service = ExternalContextService(planner=AlwaysContinuePlanner(), workflow=FakeLoopWorkflow())
            result = await service.build_context(
                query="深圳到汕头的路线和服务区",
                enabled=True,
                max_chars=2000,
                recent_messages=[],
            )

            self.assertEqual(result.diagnostics["external_agent_terminal_reason"], "max_rounds_reached")
            self.assertTrue(any("轮次上限" in notice for notice in result.notices))
            terminal = [event for event in result.tool_events if event.type == "tool_agent_terminal"]
            self.assertEqual(len(terminal), 1)
            self.assertEqual(terminal[0].payload["round"], 2)

        import asyncio

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
