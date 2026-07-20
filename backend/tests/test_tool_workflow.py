from __future__ import annotations

import asyncio
import unittest

from app.services.tools.registry import ToolRegistry
from app.services.tools.schemas import ExternalSource, PlannedToolCall, ToolCallResult, ToolPlan, ToolTraceEvent
from app.services.tools.workflow import ToolWorkflowService


class FakeWorkflowExecutor:
    def __init__(self) -> None:
        self.calls: list[PlannedToolCall] = []

    async def execute(self, call: PlannedToolCall):
        self.calls.append(call)
        return (
            ToolCallResult(
                call=call,
                status="success",
                sources=[
                    ExternalSource(
                        source_type=call.category,
                        provider=call.provider,
                        title=f"{call.display_name}结果",
                        display_text=f"{call.tool_key} result",
                    )
                ],
                elapsed_ms=1,
            ),
            [
                ToolTraceEvent(
                    type="tool_call_end",
                    payload={
                        "call_id": call.call_id,
                        "tool_key": call.tool_key,
                        "status": "success",
                        "sources_count": 1,
                    },
                )
            ],
        )


class FailingFirstExecutor(FakeWorkflowExecutor):
    async def execute(self, call: PlannedToolCall):
        if call.call_id == "first":
            self.calls.append(call)
            return (
                ToolCallResult(
                    call=call,
                    status="failed",
                    sources=[],
                    elapsed_ms=1,
                    error_message="simulated failure",
                ),
                [],
            )
        return await super().execute(call)


class ToolWorkflowTest(unittest.TestCase):
    def test_executes_multiple_planned_calls_with_limits(self) -> None:
        async def run_test() -> None:
            executor = FakeWorkflowExecutor()
            workflow = ToolWorkflowService(executor=executor, registry=ToolRegistry(), max_tool_calls=2)
            plan = ToolPlan(
                plan_id="plan-1",
                router="llm_tool_planner_v1",
                external_context_allowed=True,
                should_use_tools=True,
                calls=[
                    PlannedToolCall(
                        call_id="call-1",
                        tool_key="amap.maps.direction.driving",
                        provider="amap",
                        category="map_route",
                        display_name="高德路线",
                        confidence=0.9,
                        reason="route",
                        arguments={"query": "深圳到汕头"},
                    ),
                    PlannedToolCall(
                        call_id="call-2",
                        tool_key="amap.maps.weather",
                        provider="amap",
                        category="weather",
                        display_name="高德天气",
                        confidence=0.8,
                        reason="weather",
                        arguments={"city": "深圳", "query": "深圳天气"},
                    ),
                    PlannedToolCall(
                        call_id="call-3",
                        tool_key="web.tavily.search",
                        provider="tavily",
                        category="web_search",
                        display_name="Tavily 搜索",
                        confidence=0.7,
                        reason="web",
                        arguments={"query": "深圳到汕头"},
                    ),
                ],
            )

            result = await workflow.run(plan=plan, query="深圳到汕头路上天气怎么样")

            self.assertEqual(len(executor.calls), 2)
            self.assertEqual(len(result.sources), 2)
            self.assertEqual(result.selected_tool, "weather")
            event_types = [event.type for event in result.events]
            self.assertIn("tool_workflow_start", event_types)
            self.assertIn("tool_workflow_batch", event_types)
            self.assertIn("tool_workflow_step", event_types)
            self.assertIn("tool_workflow_end", event_types)
            batch = [event for event in result.events if event.type == "tool_workflow_batch"][0]
            self.assertEqual(batch.payload["mode"], "parallel")

        asyncio.run(run_test())

    def test_respects_dependencies_between_calls(self) -> None:
        async def run_test() -> None:
            executor = FakeWorkflowExecutor()
            workflow = ToolWorkflowService(executor=executor, registry=ToolRegistry(), max_tool_calls=3)
            plan = ToolPlan(
                plan_id="plan-deps",
                router="llm_tool_planner_v1",
                external_context_allowed=True,
                should_use_tools=True,
                calls=[
                    PlannedToolCall(
                        call_id="route",
                        tool_key="amap.maps.direction.driving",
                        provider="amap",
                        category="map_route",
                        display_name="高德路线",
                        confidence=0.9,
                        reason="route first",
                        arguments={"origin": "深圳", "destination": "汕头"},
                    ),
                    PlannedToolCall(
                        call_id="service_areas",
                        tool_key="web.tavily.search",
                        provider="tavily",
                        category="web_search",
                        display_name="Tavily 搜索",
                        confidence=0.8,
                        reason="search after route",
                        arguments={"query": "深圳到汕头 沿途 服务区"},
                        depends_on=["route"],
                    ),
                ],
            )

            result = await workflow.run(plan=plan, query="深圳到汕头路上有哪些服务区")

            self.assertEqual([call.call_id for call in executor.calls], ["route", "service_areas"])
            batches = [event for event in result.events if event.type == "tool_workflow_batch"]
            self.assertEqual(len(batches), 2)
            self.assertEqual(batches[0].payload["call_ids"], ["route"])
            self.assertEqual(batches[1].payload["call_ids"], ["service_areas"])

        asyncio.run(run_test())

    def test_skips_duplicate_tool_and_arguments_within_one_plan(self) -> None:
        async def run_test() -> None:
            executor = FakeWorkflowExecutor()
            workflow = ToolWorkflowService(executor=executor, registry=ToolRegistry())
            common = {
                "tool_key": "amap.maps.weather",
                "provider": "amap",
                "category": "weather",
                "display_name": "高德天气",
                "confidence": 0.9,
                "reason": "weather",
                "arguments": {"city": "深圳"},
            }
            plan = ToolPlan(
                plan_id="plan-duplicate",
                router="llm_tool_planner_v1",
                external_context_allowed=True,
                should_use_tools=True,
                calls=[
                    PlannedToolCall(call_id="weather-1", **common),
                    PlannedToolCall(call_id="weather-2", **common),
                ],
            )

            result = await workflow.run(plan=plan, query="深圳天气")

            self.assertEqual([call.call_id for call in executor.calls], ["weather-1"])
            skipped = [event for event in result.events if event.type == "tool_workflow_step_skipped"]
            self.assertEqual(skipped[0].payload["reason"], "duplicate_tool_call")

        asyncio.run(run_test())

    def test_stops_cyclic_dependencies_without_executing_calls(self) -> None:
        async def run_test() -> None:
            executor = FakeWorkflowExecutor()
            workflow = ToolWorkflowService(executor=executor, registry=ToolRegistry())
            plan = ToolPlan(
                plan_id="plan-cycle",
                router="llm_tool_planner_v1",
                external_context_allowed=True,
                should_use_tools=True,
                calls=[
                    PlannedToolCall(
                        call_id="a",
                        tool_key="amap.maps.weather",
                        provider="amap",
                        category="weather",
                        display_name="A",
                        confidence=0.9,
                        reason="cycle",
                        arguments={"city": "深圳"},
                        depends_on=["b"],
                    ),
                    PlannedToolCall(
                        call_id="b",
                        tool_key="web.tavily.search",
                        provider="tavily",
                        category="web_search",
                        display_name="B",
                        confidence=0.9,
                        reason="cycle",
                        arguments={"query": "深圳天气"},
                        depends_on=["a"],
                    ),
                ],
            )

            result = await workflow.run(plan=plan, query="深圳天气")

            self.assertEqual(executor.calls, [])
            skipped = [event for event in result.events if event.type == "tool_workflow_step_skipped"]
            self.assertEqual(len(skipped), 2)
            self.assertTrue(all(event.payload["reason"] == "unresolved_or_cyclic_dependencies" for event in skipped))

        asyncio.run(run_test())

    def test_failed_dependency_skips_downstream_call(self) -> None:
        async def run_test() -> None:
            executor = FailingFirstExecutor()
            workflow = ToolWorkflowService(executor=executor, registry=ToolRegistry())
            plan = ToolPlan(
                plan_id="plan-failed-dependency",
                router="llm_tool_planner_v1",
                external_context_allowed=True,
                should_use_tools=True,
                calls=[
                    PlannedToolCall(
                        call_id="first",
                        tool_key="amap.maps.weather",
                        provider="amap",
                        category="weather",
                        display_name="First",
                        confidence=0.9,
                        reason="fails",
                        arguments={"city": "深圳"},
                    ),
                    PlannedToolCall(
                        call_id="second",
                        tool_key="web.tavily.search",
                        provider="tavily",
                        category="web_search",
                        display_name="Second",
                        confidence=0.9,
                        reason="runs after first completes",
                        arguments={"query": "深圳天气"},
                        depends_on=["first"],
                    ),
                ],
            )

            result = await workflow.run(plan=plan, query="深圳天气")

            self.assertEqual([call.call_id for call in executor.calls], ["first"])
            skipped = [event for event in result.events if event.type == "tool_workflow_step_skipped"]
            self.assertEqual(skipped[0].payload["reason"], "failed_dependencies")
            self.assertEqual(skipped[0].payload["failed_dependencies"], ["first"])

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
