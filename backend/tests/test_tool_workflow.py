from __future__ import annotations

import asyncio
import unittest

from app.services.tools.catalog import ToolCatalog
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


class RaisingExecutor(FakeWorkflowExecutor):
    async def execute(self, call: PlannedToolCall):
        self.calls.append(call)
        if call.call_id == "broken":
            raise RuntimeError("internal endpoint and secret must not escape")
        return await super().execute(call)


class RaisingFallbackExecutor(FakeWorkflowExecutor):
    async def execute(self, call: PlannedToolCall):
        self.calls.append(call)
        if call.tool_key == "web.tavily.search":
            raise RuntimeError("fallback internal secret")
        return (
            ToolCallResult(
                call=call,
                status="failed",
                sources=[],
                elapsed_ms=1,
                error_message="primary failed",
            ),
            [],
        )


class ToolWorkflowTest(unittest.TestCase):
    def test_stable_arguments_use_canonical_nested_json(self) -> None:
        common = {
            "call_id": "call",
            "tool_key": "test.tool",
            "provider": "test",
            "category": "test",
            "display_name": "Test",
            "confidence": 1.0,
            "reason": "test",
        }
        first = PlannedToolCall(arguments={"filter": {"b": 2, "a": 1}}, **common)
        second = PlannedToolCall(arguments={"filter": {"a": 1, "b": 2}}, **common)

        self.assertEqual(
            ToolWorkflowService._stable_arguments(first),
            ToolWorkflowService._stable_arguments(second),
        )

    def test_executes_multiple_planned_calls_with_limits(self) -> None:
        async def run_test() -> None:
            executor = FakeWorkflowExecutor()
            workflow = ToolWorkflowService(executor=executor, registry=ToolCatalog(), max_tool_calls=2)
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
            workflow = ToolWorkflowService(executor=executor, registry=ToolCatalog(), max_tool_calls=3)
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
            workflow = ToolWorkflowService(executor=executor, registry=ToolCatalog())
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

    def test_duplicate_call_id_is_not_silently_overwritten(self) -> None:
        async def run_test() -> None:
            executor = FakeWorkflowExecutor()
            workflow = ToolWorkflowService(executor=executor, registry=ToolCatalog())
            plan = ToolPlan(
                plan_id="plan-duplicate-id",
                router="test",
                external_context_allowed=True,
                should_use_tools=True,
                calls=[
                    PlannedToolCall(
                        call_id="same",
                        tool_key="amap.maps.weather",
                        provider="amap",
                        category="weather",
                        display_name="深圳天气",
                        confidence=1.0,
                        reason="first",
                        arguments={"city": "深圳"},
                    ),
                    PlannedToolCall(
                        call_id="same",
                        tool_key="amap.maps.weather",
                        provider="amap",
                        category="weather",
                        display_name="广州天气",
                        confidence=1.0,
                        reason="second",
                        arguments={"city": "广州"},
                    ),
                ],
            )

            result = await workflow.run(plan=plan, query="天气")

            self.assertEqual([call.arguments["city"] for call in executor.calls], ["深圳"])
            skipped = [event for event in result.events if event.type == "tool_workflow_step_skipped"]
            self.assertEqual(skipped[0].payload["reason"], "duplicate_call_id")

        asyncio.run(run_test())

    def test_executor_exception_is_isolated_and_sanitized(self) -> None:
        async def run_test() -> None:
            executor = RaisingExecutor()
            workflow = ToolWorkflowService(executor=executor, registry=ToolCatalog())
            plan = ToolPlan(
                plan_id="plan-isolation",
                router="test",
                external_context_allowed=True,
                should_use_tools=True,
                calls=[
                    PlannedToolCall(
                        call_id=call_id,
                        tool_key="amap.maps.weather",
                        provider="amap",
                        category="weather",
                        display_name=display_name,
                        confidence=1.0,
                        reason="parallel",
                        arguments={"city": city},
                    )
                    for call_id, display_name, city in [
                        ("broken", "故障天气", "深圳"),
                        ("healthy", "正常天气", "广州"),
                    ]
                ],
            )

            result = await workflow.run(plan=plan, query="天气")

            self.assertEqual(len(result.sources), 1)
            errors = [event for event in result.events if event.type == "tool_call_error"]
            self.assertEqual(len(errors), 1)
            self.assertNotIn("secret", errors[0].payload["error"])
            self.assertIn("正常天气", result.sources[0].title)

        asyncio.run(run_test())

    def test_fallback_is_deduplicated_and_counted_in_hard_budget(self) -> None:
        async def run_test() -> None:
            executor = FailingFirstExecutor()
            workflow = ToolWorkflowService(executor=executor, registry=ToolCatalog(), max_tool_calls=2)
            plan = ToolPlan(
                plan_id="plan-fallback-budget",
                router="test",
                external_context_allowed=True,
                should_use_tools=True,
                fallback_tool_key="web.tavily.search",
                calls=[
                    PlannedToolCall(
                        call_id="first",
                        tool_key="amap.maps.weather",
                        provider="amap",
                        category="weather",
                        display_name="高德天气",
                        confidence=1.0,
                        reason="primary",
                        arguments={"city": "深圳"},
                    ),
                    PlannedToolCall(
                        call_id="planned-web",
                        tool_key="web.tavily.search",
                        provider="tavily",
                        category="web_search",
                        display_name="Tavily 搜索",
                        confidence=1.0,
                        reason="already planned",
                        arguments={"query": "深圳天气"},
                    ),
                ],
            )

            await workflow.run(plan=plan, query="深圳天气")

            self.assertEqual(len(executor.calls), 2)
            self.assertEqual([call.tool_key for call in executor.calls].count("web.tavily.search"), 1)

        asyncio.run(run_test())

    def test_web_search_never_falls_back_to_itself(self) -> None:
        async def run_test() -> None:
            executor = FailingFirstExecutor()
            workflow = ToolWorkflowService(executor=executor, registry=ToolCatalog(), max_tool_calls=5)
            plan = ToolPlan(
                plan_id="plan-no-self-fallback",
                router="test",
                external_context_allowed=True,
                should_use_tools=True,
                fallback_tool_key="web.tavily.search",
                calls=[
                    PlannedToolCall(
                        call_id="first",
                        tool_key="web.tavily.search",
                        provider="tavily",
                        category="web_search",
                        display_name="Tavily 搜索",
                        confidence=1.0,
                        reason="primary",
                        arguments={"query": "深圳天气"},
                    )
                ],
            )

            await workflow.run(plan=plan, query="深圳天气")

            self.assertEqual(len(executor.calls), 1)

        asyncio.run(run_test())

    def test_fallback_executor_exception_is_isolated_and_sanitized(self) -> None:
        async def run_test() -> None:
            executor = RaisingFallbackExecutor()
            workflow = ToolWorkflowService(executor=executor, registry=ToolCatalog(), max_tool_calls=2)
            plan = ToolPlan(
                plan_id="plan-fallback-exception",
                router="test",
                external_context_allowed=True,
                should_use_tools=True,
                fallback_tool_key="web.tavily.search",
                calls=[
                    PlannedToolCall(
                        call_id="weather",
                        tool_key="amap.maps.weather",
                        provider="amap",
                        category="weather",
                        display_name="高德天气",
                        confidence=1.0,
                        reason="primary",
                        arguments={"city": "深圳"},
                    )
                ],
            )

            result = await workflow.run(plan=plan, query="深圳天气")

            errors = [event for event in result.events if event.type == "tool_call_error"]
            self.assertEqual(len(errors), 1)
            self.assertNotIn("secret", errors[0].payload["error"])
            self.assertEqual(result.sources, [])

        asyncio.run(run_test())

    def test_web_fallback_does_not_satisfy_structured_dependency_contract(self) -> None:
        async def run_test() -> None:
            executor = FailingFirstExecutor()
            workflow = ToolWorkflowService(executor=executor, registry=ToolCatalog(), max_tool_calls=3)
            plan = ToolPlan(
                plan_id="plan-fallback-contract",
                router="test",
                external_context_allowed=True,
                should_use_tools=True,
                fallback_tool_key="web.tavily.search",
                calls=[
                    PlannedToolCall(
                        call_id="first",
                        tool_key="amap.maps.weather",
                        provider="amap",
                        category="weather",
                        display_name="高德天气",
                        confidence=1.0,
                        reason="primary",
                        arguments={"city": "深圳"},
                    ),
                    PlannedToolCall(
                        call_id="downstream",
                        tool_key="amap.maps.text_search",
                        provider="amap",
                        category="map_poi",
                        display_name="依赖天气的下游",
                        confidence=1.0,
                        reason="depends on structured result",
                        arguments={"keywords": "公园"},
                        depends_on=["first"],
                    ),
                ],
            )

            result = await workflow.run(plan=plan, query="深圳天气和公园")

            self.assertEqual(len(result.sources), 1)
            self.assertNotIn("downstream", [call.call_id for call in executor.calls])
            skipped = [event for event in result.events if event.type == "tool_workflow_step_skipped"]
            self.assertEqual(skipped[-1].payload["reason"], "failed_dependencies")

        asyncio.run(run_test())

    def test_stops_cyclic_dependencies_without_executing_calls(self) -> None:
        async def run_test() -> None:
            executor = FakeWorkflowExecutor()
            workflow = ToolWorkflowService(executor=executor, registry=ToolCatalog())
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
            workflow = ToolWorkflowService(executor=executor, registry=ToolCatalog())
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
