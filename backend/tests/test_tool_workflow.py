from __future__ import annotations

import asyncio
import unittest

from app.services.tools.catalog import ToolCatalog
from app.services.tools.schemas import (
    ExternalSource,
    PlannedToolCall,
    ToolCallResult,
    ToolDefinition,
    ToolPlan,
    ToolResultBinding,
    ToolTraceEvent,
)
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


class QualityGateExecutor(FakeWorkflowExecutor):
    def __init__(self, quality_status: str) -> None:
        super().__init__()
        self.quality_status = quality_status

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
                        display_text="低质量但非空结果",
                    )
                ],
                elapsed_ms=1,
                quality_status=self.quality_status,
                quality_reasons=[f"test_{self.quality_status}"],
            ),
            [],
        )


class NonSuccessSourceExecutor(FakeWorkflowExecutor):
    async def execute(self, call: PlannedToolCall):
        self.calls.append(call)
        return (
            ToolCallResult(
                call=call,
                status="failed",
                sources=[
                    ExternalSource(
                        source_type=call.category,
                        provider=call.provider,
                        title="failed evidence",
                        display_text="must not reach the answer prompt",
                    )
                ],
                elapsed_ms=1,
                error_message="simulated failure",
            ),
            [],
        )


class ConfirmationEvidenceExecutor(FakeWorkflowExecutor):
    async def execute(self, call: PlannedToolCall):
        self.calls.append(call)
        return (
            ToolCallResult(
                call=call,
                status="confirmation_required",
                sources=[
                    ExternalSource(
                        source_type="workspace_file_edit_approval",
                        provider="workspace",
                        title="pending diff",
                        display_text="awaiting confirmation",
                    )
                ],
                elapsed_ms=1,
                error_message="waiting for confirmation",
            ),
            [
                ToolTraceEvent(
                    type="tool_confirmation_required",
                    payload={"status": "waiting_approval"},
                )
            ],
        )


class SameCategoryFallbackContractExecutor(FakeWorkflowExecutor):
    async def execute(self, call: PlannedToolCall):
        self.calls.append(call)
        if call.tool_key == "test.primary":
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
        return (
            ToolCallResult(
                call=call,
                status="success",
                sources=[
                    ExternalSource(
                        source_type="lookup",
                        provider="test",
                        title="fallback",
                        display_text="fallback evidence",
                        metadata={"raw": {"other": "value"}},
                    )
                ],
                elapsed_ms=1,
            ),
            [],
        )


class StructuredBindingExecutor(FakeWorkflowExecutor):
    async def execute(self, call: PlannedToolCall):
        self.calls.append(call)
        metadata = {}
        if call.call_id == "geo":
            metadata = {"raw": {"geocodes": [{"location": "114.0579,22.5431"}]}}
        return (
            ToolCallResult(
                call=call,
                status="success",
                sources=[
                    ExternalSource(
                        source_type=call.category,
                        provider=call.provider,
                        title=f"{call.display_name}结果",
                        display_text="structured result",
                        metadata=metadata,
                    )
                ],
                elapsed_ms=1,
            ),
            [],
        )


class ToolWorkflowTest(unittest.TestCase):
    @staticmethod
    def _binding_catalog() -> ToolCatalog:
        catalog = ToolCatalog()
        catalog._definitions = {
            "test.geo": ToolDefinition(
                tool_key="test.geo",
                provider="test",
                category="map",
                display_name="地理编码",
                description="resolve location",
                input_schema={
                    "type": "object",
                    "properties": {"address": {"type": "string"}},
                    "required": ["address"],
                    "additionalProperties": False,
                },
                adapter={"auth_type": "none"},
            ),
            "test.route": ToolDefinition(
                tool_key="test.route",
                provider="test",
                category="map",
                display_name="路线",
                description="route lookup",
                input_schema={
                    "type": "object",
                    "properties": {
                        "origin": {"type": "string"},
                        "destination": {"type": "string"},
                    },
                    "required": ["origin", "destination"],
                    "additionalProperties": False,
                },
                adapter={"auth_type": "none"},
            ),
        }
        return catalog

    def test_binds_structured_upstream_value_into_downstream_argument(self) -> None:
        async def run_test() -> None:
            executor = StructuredBindingExecutor()
            workflow = ToolWorkflowService(executor=executor, registry=self._binding_catalog())
            plan = ToolPlan(
                plan_id="plan-binding",
                router="test",
                external_context_allowed=True,
                should_use_tools=True,
                calls=[
                    PlannedToolCall(
                        call_id="geo",
                        tool_key="test.geo",
                        provider="test",
                        category="map",
                        display_name="地理编码",
                        confidence=1.0,
                        reason="first",
                        arguments={"address": "深圳"},
                    ),
                    PlannedToolCall(
                        call_id="route",
                        tool_key="test.route",
                        provider="test",
                        category="map",
                        display_name="路线",
                        confidence=1.0,
                        reason="second",
                        arguments={"origin": "113.2644,23.1291"},
                        depends_on=["geo"],
                        can_parallel=False,
                        result_bindings=[
                            ToolResultBinding(
                                source_call_id="geo",
                                source_path="/sources/0/metadata/raw/geocodes/0/location",
                                target_argument="destination",
                            )
                        ],
                    ),
                ],
            )

            result = await workflow.run(plan=plan, query="广州到深圳")

            self.assertEqual([call.call_id for call in executor.calls], ["geo", "route"])
            self.assertEqual(executor.calls[1].arguments["destination"], "114.0579,22.5431")
            binding_events = [event for event in result.events if event.type == "tool_result_binding"]
            self.assertEqual(binding_events[0].payload["status"], "resolved")

        asyncio.run(run_test())

    def test_missing_required_binding_skips_downstream_execution(self) -> None:
        async def run_test() -> None:
            executor = StructuredBindingExecutor()
            workflow = ToolWorkflowService(executor=executor, registry=self._binding_catalog())
            plan = ToolPlan(
                plan_id="plan-missing-binding",
                router="test",
                external_context_allowed=True,
                should_use_tools=True,
                calls=[
                    PlannedToolCall(
                        call_id="geo",
                        tool_key="test.geo",
                        provider="test",
                        category="map",
                        display_name="地理编码",
                        confidence=1.0,
                        reason="first",
                        arguments={"address": "深圳"},
                    ),
                    PlannedToolCall(
                        call_id="route",
                        tool_key="test.route",
                        provider="test",
                        category="map",
                        display_name="路线",
                        confidence=1.0,
                        reason="second",
                        arguments={"origin": "113.2644,23.1291"},
                        depends_on=["geo"],
                        can_parallel=False,
                        result_bindings=[
                            ToolResultBinding(
                                source_call_id="geo",
                                source_path="/sources/0/metadata/raw/geocodes/9/location",
                                target_argument="destination",
                            )
                        ],
                    ),
                ],
            )

            result = await workflow.run(plan=plan, query="广州到深圳")

            self.assertEqual([call.call_id for call in executor.calls], ["geo"])
            failed = [
                event
                for event in result.events
                if event.type == "tool_result_binding" and event.payload.get("status") == "failed"
            ]
            self.assertEqual(len(failed), 1)

        asyncio.run(run_test())

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

    def test_unbound_call_does_not_require_workflow_catalog_definition(self) -> None:
        async def run_test() -> None:
            executor = FakeWorkflowExecutor()
            catalog = ToolCatalog()
            catalog._definitions = {}
            workflow = ToolWorkflowService(executor=executor, registry=catalog)
            plan = ToolPlan(
                plan_id="plan-dynamic-unbound",
                router="test",
                external_context_allowed=True,
                should_use_tools=True,
                calls=[
                    PlannedToolCall(
                        call_id="dynamic",
                        tool_key="dynamic.test.tool",
                        provider="dynamic",
                        category="test",
                        display_name="Dynamic",
                        confidence=1.0,
                        reason="executor owns final catalog enforcement",
                        arguments={"query": "test"},
                    )
                ],
            )

            await workflow.run(plan=plan, query="test")

            self.assertEqual([call.call_id for call in executor.calls], ["dynamic"])

        asyncio.run(run_test())

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

    def test_high_risk_fallback_is_not_automatically_executed(self) -> None:
        async def run_test() -> None:
            catalog = ToolCatalog()
            fallback = catalog.get("web.tavily.search")
            fallback.risk_level = "high"
            fallback.read_only = False
            executor = FailingFirstExecutor()
            workflow = ToolWorkflowService(executor=executor, registry=catalog, max_tool_calls=2)
            plan = ToolPlan(
                plan_id="plan-high-risk-fallback",
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
                        display_name="Weather",
                        confidence=1.0,
                        reason="weather",
                    )
                ],
            )

            result = await workflow.run(plan=plan, query="深圳天气")

            self.assertEqual([call.call_id for call in executor.calls], ["first"])
            decision = [event for event in result.events if event.type == "tool_result_quality_decision"]
            self.assertEqual(decision[0].payload["fallback_available"], False)
            self.assertEqual(decision[0].payload["action"], "replan")

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

    def test_invalid_quality_blocks_downstream_dependency(self) -> None:
        async def run_test() -> None:
            executor = QualityGateExecutor("invalid")
            workflow = ToolWorkflowService(executor=executor, registry=ToolCatalog())
            plan = ToolPlan(
                plan_id="plan-invalid-quality",
                router="test",
                external_context_allowed=True,
                should_use_tools=True,
                calls=[
                    PlannedToolCall(
                        call_id="first",
                        tool_key="amap.maps.weather",
                        provider="amap",
                        category="weather",
                        display_name="First",
                        confidence=1.0,
                        reason="invalid result",
                        arguments={"city": "深圳"},
                    ),
                    PlannedToolCall(
                        call_id="second",
                        tool_key="web.tavily.search",
                        provider="tavily",
                        category="web_search",
                        display_name="Second",
                        confidence=1.0,
                        reason="requires first",
                        arguments={"query": "深圳天气"},
                        depends_on=["first"],
                    ),
                ],
            )

            result = await workflow.run(plan=plan, query="深圳天气")

            self.assertEqual([call.call_id for call in executor.calls], ["first"])
            blocked = [event for event in result.events if event.type == "quality_gate_blocked"]
            self.assertTrue(blocked)
            self.assertEqual(blocked[-1].payload["dependency_quality"]["first"], "invalid")
            decisions = [event for event in result.events if event.type == "tool_result_quality_decision"]
            self.assertEqual(decisions[0].payload["action"], "replan")
            self.assertEqual(result.sources, [])
            suppressed = [event for event in result.events if event.type == "quality_evidence_suppressed"]
            self.assertEqual(suppressed[-1].payload["exposed_to_prompt"], False)

        asyncio.run(run_test())

    def test_uncertain_quality_does_not_unlock_downstream_dependency(self) -> None:
        async def run_test() -> None:
            executor = QualityGateExecutor("uncertain")
            workflow = ToolWorkflowService(executor=executor, registry=ToolCatalog())
            plan = ToolPlan(
                plan_id="plan-uncertain-quality",
                router="test",
                external_context_allowed=True,
                should_use_tools=True,
                calls=[
                    PlannedToolCall(
                        call_id="first",
                        tool_key="amap.maps.weather",
                        provider="amap",
                        category="weather",
                        display_name="First",
                        confidence=1.0,
                        reason="uncertain result",
                        arguments={"city": "深圳"},
                    ),
                    PlannedToolCall(
                        call_id="second",
                        tool_key="web.tavily.search",
                        provider="tavily",
                        category="web_search",
                        display_name="Second",
                        confidence=1.0,
                        reason="requires first",
                        arguments={"query": "深圳天气"},
                        depends_on=["first"],
                    ),
                ],
            )

            result = await workflow.run(plan=plan, query="深圳天气")

            self.assertEqual([call.call_id for call in executor.calls], ["first"])
            blocked = [event for event in result.events if event.type == "quality_gate_blocked"]
            self.assertTrue(blocked)
            self.assertEqual(blocked[-1].payload["dependency_quality"]["first"], "uncertain")
            decisions = [event for event in result.events if event.type == "tool_result_quality_decision"]
            self.assertEqual(decisions[0].payload["action"], "replan")
            self.assertEqual(result.sources, [])

        asyncio.run(run_test())

    def test_unknown_quality_status_is_suppressed_and_blocks_downstream(self) -> None:
        async def run_test() -> None:
            executor = QualityGateExecutor("unexpected")
            workflow = ToolWorkflowService(executor=executor, registry=ToolCatalog())
            plan = ToolPlan(
                plan_id="plan-unknown-quality",
                router="test",
                external_context_allowed=True,
                should_use_tools=True,
                calls=[
                    PlannedToolCall(
                        call_id="first",
                        tool_key="amap.maps.weather",
                        provider="amap",
                        category="weather",
                        display_name="First",
                        confidence=1.0,
                        reason="unexpected result status",
                        arguments={"city": "深圳"},
                    ),
                    PlannedToolCall(
                        call_id="second",
                        tool_key="web.tavily.search",
                        provider="tavily",
                        category="web_search",
                        display_name="Second",
                        confidence=1.0,
                        reason="requires first",
                        arguments={"query": "深圳天气"},
                        depends_on=["first"],
                    ),
                ],
            )

            result = await workflow.run(plan=plan, query="深圳天气")

            self.assertEqual([call.call_id for call in executor.calls], ["first"])
            self.assertEqual(result.sources, [])
            blocked = [event for event in result.events if event.type == "quality_gate_blocked"]
            self.assertEqual(blocked[-1].payload["dependency_quality"]["first"], "invalid")
            suppressed = [event for event in result.events if event.type == "quality_evidence_suppressed"]
            self.assertEqual(suppressed[-1].payload["reason"], "quality_gate")

        asyncio.run(run_test())

    def test_non_success_sources_are_not_exposed_to_prompt(self) -> None:
        async def run_test() -> None:
            executor = NonSuccessSourceExecutor()
            workflow = ToolWorkflowService(executor=executor, registry=ToolCatalog())
            plan = ToolPlan(
                plan_id="plan-failed-evidence",
                router="test",
                external_context_allowed=True,
                should_use_tools=True,
                calls=[
                    PlannedToolCall(
                        call_id="failed",
                        tool_key="web.tavily.search",
                        provider="tavily",
                        category="web_search",
                        display_name="Search",
                        confidence=1.0,
                        reason="failed evidence",
                        arguments={"query": "test"},
                    )
                ],
            )

            result = await workflow.run(plan=plan, query="test")

            self.assertEqual(result.sources, [])
            suppressed = [event for event in result.events if event.type == "tool_evidence_suppressed"]
            self.assertEqual(len(suppressed), 1)
            self.assertEqual(suppressed[0].payload["reason"], "non_success_result")

        asyncio.run(run_test())

    def test_confirmation_diff_is_explicitly_exposed_as_evidence(self) -> None:
        async def run_test() -> None:
            executor = ConfirmationEvidenceExecutor()
            workflow = ToolWorkflowService(executor=executor, registry=ToolCatalog())
            plan = ToolPlan(
                plan_id="plan-confirmation-evidence",
                router="test",
                external_context_allowed=True,
                should_use_tools=True,
                calls=[
                    PlannedToolCall(
                        call_id="edit",
                        tool_key="workspace.files.edit",
                        provider="workspace",
                        category="workspace_file_edit",
                        display_name="Edit",
                        confidence=1.0,
                        reason="edit",
                        arguments={"file_id": "file-1"},
                    )
                ],
            )

            result = await workflow.run(plan=plan, query="edit")

            self.assertEqual(len(result.sources), 1)
            self.assertEqual(result.sources[0].title, "pending diff")
            self.assertEqual(
                [event for event in result.events if event.type == "tool_evidence_suppressed"],
                [],
            )
            decision = [event for event in result.events if event.type == "tool_result_quality_decision"]
            self.assertEqual(decision[0].payload["status"], "uncertain")
            self.assertEqual(decision[0].payload["action"], "clarify")

        asyncio.run(run_test())

    def test_same_category_fallback_must_satisfy_parent_quality_contract(self) -> None:
        async def run_test() -> None:
            catalog = ToolCatalog()
            catalog._definitions = {
                "test.primary": ToolDefinition(
                    tool_key="test.primary",
                    provider="test",
                    category="lookup",
                    display_name="Primary",
                    description="primary",
                    quality_contract={
                        "required_paths": ["/sources/0/metadata/raw/value"],
                    },
                    adapter={"auth_type": "none"},
                ),
                "test.fallback": ToolDefinition(
                    tool_key="test.fallback",
                    provider="test",
                    category="lookup",
                    display_name="Fallback",
                    description="fallback",
                    adapter={"auth_type": "none"},
                ),
                "test.downstream": ToolDefinition(
                    tool_key="test.downstream",
                    provider="test",
                    category="lookup",
                    display_name="Downstream",
                    description="downstream",
                    adapter={"auth_type": "none"},
                ),
            }
            executor = SameCategoryFallbackContractExecutor()
            workflow = ToolWorkflowService(executor=executor, registry=catalog, max_tool_calls=3)
            plan = ToolPlan(
                plan_id="plan-fallback-quality-contract",
                router="test",
                external_context_allowed=True,
                should_use_tools=True,
                fallback_tool_key="test.fallback",
                calls=[
                    PlannedToolCall(
                        call_id="primary",
                        tool_key="test.primary",
                        provider="test",
                        category="lookup",
                        display_name="Primary",
                        confidence=1.0,
                        reason="primary",
                    ),
                    PlannedToolCall(
                        call_id="downstream",
                        tool_key="test.downstream",
                        provider="test",
                        category="lookup",
                        display_name="Downstream",
                        confidence=1.0,
                        reason="requires structured primary result",
                        depends_on=["primary"],
                    ),
                ],
            )

            result = await workflow.run(plan=plan, query="lookup")

            self.assertEqual(executor.calls[0].call_id, "primary")
            self.assertEqual(executor.calls[1].tool_key, "test.fallback")
            self.assertEqual(len(executor.calls), 2)
            blocked = [
                event
                for event in result.events
                if event.type == "quality_gate_blocked"
                and event.payload.get("stage") == "fallback_dependency_contract"
            ]
            self.assertEqual(len(blocked), 1)
            fallback_decisions = [
                event
                for event in result.events
                if event.type == "tool_result_quality_decision"
                and event.payload.get("stage") == "fallback"
            ]
            self.assertEqual(fallback_decisions[0].payload["status"], "valid")
            self.assertEqual(fallback_decisions[0].payload["action"], "continue")

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
