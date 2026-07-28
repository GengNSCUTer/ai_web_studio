from __future__ import annotations

import asyncio
import json
import unittest

from app.services.tools.catalog import ToolCatalog
from app.services.tools.planner import DeterministicToolPlanner, LLMToolPlanner, PlannerRuntime
from app.services.tools.schemas import ToolDefinition
from app.services.tools.selector import ToolCandidateSelector
from app.services.tools.validation import ToolSchemaValidationError, ToolSchemaValidator


class FakeChatProvider:
    def __init__(self, text: str) -> None:
        self.text = text
        self.kwargs = None

    async def complete_chat(self, **kwargs) -> str:
        self.kwargs = kwargs
        return self.text


class ToolPlannerTest(unittest.TestCase):
    def test_candidate_selector_picks_relevant_map_and_weather_tools(self) -> None:
        catalog = ToolCatalog()
        candidates, trace = ToolCandidateSelector(catalog, max_candidates=5).select(
            query="深圳到汕头路上有哪些服务区，顺便看天气和预计耗时",
            enabled=True,
        )

        tool_keys = {tool.tool_key for tool in candidates}
        self.assertIn("amap.maps.direction.driving", tool_keys)
        self.assertIn("amap.maps.weather", tool_keys)
        self.assertIn("amap.maps.text_search", tool_keys)
        self.assertEqual(trace["type"], "tool_candidate_selection")
        self.assertLessEqual(len(candidates), 5)

    def test_candidate_selector_does_not_select_every_read_only_tool(self) -> None:
        candidates, _ = ToolCandidateSelector(ToolCatalog()).select(
            query="深圳今天气温怎么样",
            enabled=True,
        )

        tool_keys = {tool.tool_key for tool in candidates}
        self.assertEqual(tool_keys, {"amap.maps.weather", "web.tavily.search"})
        self.assertNotIn("amap.maps.direction.driving", tool_keys)
        self.assertNotIn("amap.maps.distance", tool_keys)

    def test_candidate_selector_reserves_web_fallback_slot(self) -> None:
        candidates, _ = ToolCandidateSelector(ToolCatalog(), max_candidates=4).select(
            query="深圳到汕头怎么去，路上有哪些服务区",
            enabled=True,
        )

        self.assertLessEqual(len(candidates), 4)
        self.assertIn("web.tavily.search", {tool.tool_key for tool in candidates})

    def test_schema_validator_normalizes_array_and_enum_defaults(self) -> None:
        catalog = ToolCatalog()
        definition = catalog.get("amap.maps.distance")

        args = ToolSchemaValidator().validate(
            definition=definition,
            arguments={
                "origins": ["深圳松岗", "广州南站"],
                "destination": "汕头市潮阳区西凤村",
                "type": "bad-type",
            },
        )

        self.assertEqual(args["origins"], "深圳松岗|广州南站")
        self.assertEqual(args["type"], "bad-type")

    def test_schema_validator_rejects_missing_required_field(self) -> None:
        catalog = ToolCatalog()
        definition = catalog.get("amap.maps.direction.driving")

        with self.assertRaises(ToolSchemaValidationError):
            ToolSchemaValidator().validate(definition=definition, arguments={"origin": "深圳松岗"})

    def test_schema_validator_rejects_undeclared_field_when_schema_is_strict(self) -> None:
        definition = ToolDefinition(
            tool_key="mcp.weather.lookup",
            provider="weather",
            category="weather",
            display_name="Weather",
            description="Lookup weather",
            input_schema={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
                "additionalProperties": False,
            },
        )

        with self.assertRaisesRegex(ToolSchemaValidationError, "schema 未声明的参数：query"):
            ToolSchemaValidator().validate(
                definition=definition,
                arguments={"city": "深圳", "query": "深圳天气"},
            )

    def test_schema_validator_enforces_full_json_schema_keywords(self) -> None:
        definition = ToolDefinition(
            tool_key="mcp.strict.search",
            provider="test",
            category="web_search",
            display_name="Strict",
            description="Strict schema",
            input_schema={
                "type": "object",
                "properties": {
                    "mode": {"const": "safe"},
                    "filter": {
                        "type": "object",
                        "properties": {"country": {"type": "string", "pattern": "^[A-Z]{2}$"}},
                        "required": ["country"],
                    },
                },
                "required": ["mode", "filter"],
            },
        )

        with self.assertRaises(ToolSchemaValidationError):
            ToolSchemaValidator().validate(
                definition=definition,
                arguments={"mode": "unsafe", "filter": {"country": "china"}},
            )

    def test_planner_fixed_arguments_are_not_model_overridable(self) -> None:
        definition = ToolDefinition(
            tool_key="mcp.tenant.lookup",
            provider="test",
            category="web_search",
            display_name="Tenant lookup",
            description="Tenant-scoped lookup",
            input_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}, "tenant_id": {"type": "string"}},
                "required": ["query", "tenant_id"],
                "additionalProperties": False,
            },
            adapter={"fixed_arguments": {"tenant_id": "trusted-tenant"}},
        )
        catalog = ToolCatalog()
        catalog._definitions = {definition.tool_key: definition}
        planner = LLMToolPlanner(catalog=catalog)

        plan = planner._parse_llm_plan(
            text='{"should_use_tools":true,"calls":[{"tool_key":"mcp.tenant.lookup","arguments":{"query":"x","tenant_id":"attacker"}}]}',
            query="x",
            allowed_tool_keys={definition.tool_key},
        )

        self.assertEqual(plan.calls[0].arguments, {"query": "x"})

    def test_llm_planner_does_not_inject_query_into_strict_schema_without_query_field(self) -> None:
        definition = ToolDefinition(
            tool_key="mcp.weather.lookup",
            provider="weather",
            category="weather",
            display_name="Weather",
            description="Lookup weather",
            input_schema={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
                "additionalProperties": False,
            },
        )
        catalog = ToolCatalog()
        catalog._definitions = {definition.tool_key: definition}
        planner = LLMToolPlanner(catalog=catalog)

        plan = planner._parse_llm_plan(
            text='{"should_use_tools":true,"calls":[{"tool_key":"mcp.weather.lookup","arguments":{"city":"深圳"}}]}',
            query="深圳天气",
            allowed_tool_keys={definition.tool_key},
        )

        self.assertEqual(plan.calls[0].arguments, {"city": "深圳"})

    def test_llm_planner_parses_valid_tool_call(self) -> None:
        async def run_test() -> None:
            planner = LLMToolPlanner(
                chat_provider=FakeChatProvider(
                    """
                    {
                      "should_use_tools": true,
                      "calls": [
                        {
                          "tool_key": "web.tavily.search",
                          "confidence": 0.91,
                          "reason": "用户询问实时人物信息，需要联网搜索。",
                          "arguments": {
                            "query": "美国现任总统是谁"
                          }
                        }
                      ]
                    }
                    """
                )
            )

            plan = await planner.plan(
                query="美国现任总统是谁",
                enabled=True,
                runtime=PlannerRuntime(
                    provider_type="openai-compatible",
                    base_url="https://example.test/v1",
                    api_key="sk-test",
                    model_name="deepseek-ai/DeepSeek-V4-Flash",
                ),
            )

            self.assertEqual(plan.router, "llm_tool_planner_v1")
            self.assertEqual(plan.calls[0].tool_key, "web.tavily.search")
            self.assertEqual(plan.calls[0].arguments["query"], "美国现任总统是谁")
            event_types = [event["type"] for event in plan.trace_events]
            self.assertIn("tool_planner_start", event_types)
            self.assertIn("tool_planner_llm_output", event_types)
            self.assertIn("tool_schema_validation", event_types)
            self.assertIn("tool_planner_end", event_types)
            schema_events = [event for event in plan.trace_events if event["type"] == "tool_schema_validation"]
            self.assertEqual(schema_events[0]["status"], "passed")
            self.assertEqual(schema_events[0]["normalized_arguments"]["query"], "美国现任总统是谁")

        asyncio.run(run_test())

    def test_llm_planner_is_primary_for_map_route_when_runtime_exists(self) -> None:
        async def run_test() -> None:
            fake_provider = FakeChatProvider(
                """
                {
                  "should_use_tools": true,
                  "calls": [
                    {
                      "tool_key": "amap.maps.direction.driving",
                      "confidence": 0.92,
                      "reason": "用户询问驾车路线耗时，需要路线规划。",
                      "arguments": {
                        "origin": "深圳松岗",
                        "destination": "汕头市潮阳区西凤村",
                        "mode": "driving",
                        "query": "深圳松岗到汕头市潮阳区西凤村开车多久"
                      }
                    }
                  ]
                }
                """
            )
            planner = LLMToolPlanner(chat_provider=fake_provider)

            plan = await planner.plan(
                query="深圳松岗到汕头市潮阳区西凤村开车多久",
                enabled=True,
                runtime=PlannerRuntime(
                    provider_type="openai-compatible",
                    base_url="https://example.test/v1",
                    api_key="sk-test",
                    model_name="deepseek-ai/DeepSeek-V3",
                ),
            )

            self.assertEqual(plan.router, "llm_tool_planner_v1")
            self.assertEqual(plan.calls[0].tool_key, "amap.maps.direction.driving")
            self.assertEqual(plan.calls[0].arguments["origin"], "深圳松岗")
            event_types = [event["type"] for event in plan.trace_events]
            self.assertIn("tool_candidate_selection", event_types)
            end_event = [event for event in plan.trace_events if event["type"] == "tool_planner_end"][-1]
            self.assertEqual(end_event["strategy"], "llm_primary")

        asyncio.run(run_test())

    def test_llm_planner_parses_dependencies_and_continue_flag(self) -> None:
        async def run_test() -> None:
            planner = LLMToolPlanner(
                chat_provider=FakeChatProvider(
                    """
                    {
                      "should_use_tools": true,
                      "need_more_rounds": true,
                      "calls": [
                        {
                          "id": "route",
                          "tool_key": "amap.maps.direction.driving",
                          "confidence": 0.9,
                          "reason": "先查路线",
                          "arguments": {"origin": "深圳", "destination": "汕头"}
                        },
                        {
                          "id": "service_search",
                          "tool_key": "web.tavily.search",
                          "confidence": 0.8,
                          "reason": "路线后查服务区",
                          "depends_on": ["route"],
                          "can_parallel": false,
                          "arguments": {"query": "深圳到汕头 沿途 服务区"}
                        }
                      ]
                    }
                    """
                )
            )

            plan = await planner.plan(
                query="深圳到汕头路上有哪些服务区",
                enabled=True,
                runtime=PlannerRuntime(
                    provider_type="openai-compatible",
                    base_url="https://example.test/v1",
                    api_key="sk-test",
                    model_name="deepseek-ai/DeepSeek-V3",
                ),
            )

            self.assertTrue(plan.need_more_rounds)
            self.assertEqual(plan.calls[0].call_id, "route")
            self.assertEqual(plan.calls[1].depends_on, ["route"])
            self.assertFalse(plan.calls[1].can_parallel)

        asyncio.run(run_test())

    @staticmethod
    def _result_binding_catalog() -> ToolCatalog:
        catalog = ToolCatalog()
        catalog._definitions = {
            "test.lookup": ToolDefinition(
                tool_key="test.lookup",
                provider="test",
                category="lookup",
                display_name="Lookup",
                description="Resolve a location",
                input_schema={
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                    "additionalProperties": False,
                },
            ),
            "test.consume": ToolDefinition(
                tool_key="test.consume",
                provider="test",
                category="consumer",
                display_name="Consume",
                description="Consume a structured location",
                input_schema={
                    "type": "object",
                    "properties": {"location": {"type": "string"}},
                    "required": ["location"],
                    "additionalProperties": False,
                },
            ),
        }
        return catalog

    def test_llm_planner_defers_required_field_supplied_by_result_binding(self) -> None:
        catalog = self._result_binding_catalog()
        planner = LLMToolPlanner(catalog=catalog)

        plan = planner._parse_llm_plan(
            text="""
            {
              "should_use_tools": true,
              "calls": [
                {
                  "id": "lookup",
                  "tool_key": "test.lookup",
                  "arguments": {"query": "深圳"}
                },
                {
                  "id": "consume",
                  "tool_key": "test.consume",
                  "arguments": {},
                  "depends_on": ["lookup"],
                  "can_parallel": false,
                  "result_bindings": [{
                    "source_call_id": "lookup",
                    "source_path": "/sources/0/metadata/raw/location",
                    "target_argument": "location",
                    "required": true
                  }]
                }
              ]
            }
            """,
            query="查询深圳位置",
            allowed_tool_keys={item.tool_key for item in catalog.list_definitions()},
        )

        self.assertEqual([call.call_id for call in plan.calls], ["lookup", "consume"])
        self.assertEqual(plan.calls[1].arguments, {})
        self.assertEqual(plan.calls[1].result_bindings[0].source_call_id, "lookup")

    def test_llm_planner_rejects_unsafe_or_ambiguous_result_binding(self) -> None:
        catalog = self._result_binding_catalog()
        planner = LLMToolPlanner(catalog=catalog)
        unsafe_variants = [
            {
                "source_call_id": "lookup",
                "source_path": "/sources/0/display_text",
                "target_argument": "location",
                "required": True,
            },
            {
                "source_call_id": "lookup",
                "source_path": "/sources/0/metadata/raw/location",
                "target_argument": "undeclared",
                "required": True,
            },
            {
                "source_call_id": "lookup",
                "source_path": "/sources/0/metadata/raw/location",
                "target_argument": "location",
                "required": "true",
            },
        ]

        for binding in unsafe_variants:
            with self.subTest(binding=binding):
                plan = planner._parse_llm_plan(
                    text=json.dumps(
                        {
                            "should_use_tools": True,
                            "calls": [
                                {
                                    "id": "lookup",
                                    "tool_key": "test.lookup",
                                    "arguments": {"query": "深圳"},
                                },
                                {
                                    "id": "consume",
                                    "tool_key": "test.consume",
                                    "arguments": {},
                                    "depends_on": ["lookup"],
                                    "result_bindings": [binding],
                                },
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    query="查询深圳位置",
                    allowed_tool_keys={item.tool_key for item in catalog.list_definitions()},
                )

                self.assertEqual([call.call_id for call in plan.calls], ["lookup"])

    def test_llm_planner_requires_binding_source_dependency_and_no_argument_override(self) -> None:
        catalog = self._result_binding_catalog()
        planner = LLMToolPlanner(catalog=catalog)
        common_binding = {
            "source_call_id": "lookup",
            "source_path": "/sources/0/metadata/raw/location",
            "target_argument": "location",
            "required": True,
        }

        for arguments, depends_on in [({}, []), ({"location": "模型伪造值"}, ["lookup"])]:
            with self.subTest(arguments=arguments, depends_on=depends_on):
                plan = planner._parse_llm_plan(
                    text=json.dumps(
                        {
                            "should_use_tools": True,
                            "calls": [
                                {
                                    "id": "lookup",
                                    "tool_key": "test.lookup",
                                    "arguments": {"query": "深圳"},
                                },
                                {
                                    "id": "consume",
                                    "tool_key": "test.consume",
                                    "arguments": arguments,
                                    "depends_on": depends_on,
                                    "result_bindings": [common_binding],
                                },
                            ],
                        },
                        ensure_ascii=False,
                    ),
                    query="查询深圳位置",
                    allowed_tool_keys={item.tool_key for item in catalog.list_definitions()},
                )

                self.assertEqual([call.call_id for call in plan.calls], ["lookup"])

    def test_llm_planner_rejects_duplicate_call_ids_and_their_dependents(self) -> None:
        planner = LLMToolPlanner()
        plan = planner._parse_llm_plan(
            text="""
            {
              "should_use_tools": true,
              "calls": [
                {"id":"same", "tool_key":"amap.maps.weather", "arguments":{"city":"深圳"}},
                {"id":"same", "tool_key":"amap.maps.weather", "arguments":{"city":"广州"}},
                {"id":"after", "tool_key":"web.tavily.search", "depends_on":["missing"], "arguments":{"query":"天气"}}
              ]
            }
            """,
            query="深圳和广州天气",
        )

        self.assertEqual(len(plan.calls), 1)
        self.assertEqual(plan.calls[0].arguments["city"], "深圳")
        failed = [
            event
            for event in plan.trace_events
            if event.get("type") == "tool_schema_validation" and event.get("status") == "failed"
        ]
        self.assertTrue(any("重复" in str(event.get("error")) for event in failed))
        self.assertTrue(any("依赖" in str(event.get("error")) for event in failed))

    def test_llm_planner_handles_complex_route_weather_poi_query(self) -> None:
        async def run_test() -> None:
            planner = LLMToolPlanner(
                chat_provider=FakeChatProvider(
                    """
                    {
                      "should_use_tools": true,
                      "need_more_rounds": false,
                      "calls": [
                        {
                          "id": "route",
                          "tool_key": "amap.maps.direction.driving",
                          "confidence": 0.92,
                          "reason": "预计耗时需要路线工具",
                          "depends_on": [],
                          "can_parallel": true,
                          "arguments": {"origin": "深圳", "destination": "汕头", "query": "深圳到汕头路上有哪些服务区，顺便看天气和预计耗时"}
                        },
                        {
                          "id": "weather_origin",
                          "tool_key": "amap.maps.weather",
                          "confidence": 0.85,
                          "reason": "查询起点天气",
                          "depends_on": [],
                          "can_parallel": true,
                          "arguments": {"city": "深圳"}
                        },
                        {
                          "id": "weather_destination",
                          "tool_key": "amap.maps.weather",
                          "confidence": 0.85,
                          "reason": "查询终点天气",
                          "depends_on": [],
                          "can_parallel": true,
                          "arguments": {"city": "汕头"}
                        },
                        {
                          "id": "poi",
                          "tool_key": "amap.maps.text_search",
                          "confidence": 0.76,
                          "reason": "查询沿途服务区",
                          "depends_on": [],
                          "can_parallel": true,
                          "arguments": {"keywords": "深圳到汕头 服务区"}
                        },
                        {
                          "id": "web",
                          "tool_key": "web.tavily.search",
                          "confidence": 0.72,
                          "reason": "网页补充沿途服务区信息",
                          "depends_on": ["route"],
                          "can_parallel": false,
                          "arguments": {"query": "深圳到汕头路上有哪些服务区，顺便看天气和预计耗时"}
                        }
                      ]
                    }
                    """
                )
            )

            plan = await planner.plan(
                query="深圳到汕头路上有哪些服务区，顺便看天气和预计耗时",
                enabled=True,
                runtime=PlannerRuntime(
                    provider_type="openai-compatible",
                    base_url="https://example.test/v1",
                    api_key="sk-test",
                    model_name="deepseek-ai/DeepSeek-V4-Flash",
                ),
            )

            self.assertEqual(plan.router, "llm_tool_planner_v1")
            self.assertEqual([call.tool_key for call in plan.calls], [
                "amap.maps.direction.driving",
                "amap.maps.weather",
                "amap.maps.weather",
                "amap.maps.text_search",
                "web.tavily.search",
            ])
            self.assertEqual(plan.calls[-1].depends_on, ["route"])
            self.assertFalse(plan.calls[-1].can_parallel)
            end_event = [event for event in plan.trace_events if event["type"] == "tool_planner_end"][-1]
            self.assertEqual(end_event["strategy"], "llm_primary")

        asyncio.run(run_test())

    def test_deterministic_fallback_splits_route_weather_and_service_area_query(self) -> None:
        plan = DeterministicToolPlanner().plan(
            query="深圳到汕头路上有哪些服务区，顺便看天气和预计耗时",
            enabled=True,
        )

        calls = {call.tool_key: call for call in plan.calls}
        self.assertEqual(plan.router, "deterministic_tool_planner_v2")
        self.assertIn("amap.maps.direction.driving", calls)
        self.assertIn("amap.maps.weather", calls)
        self.assertIn("amap.maps.text_search", calls)
        self.assertIn("web.tavily.search", calls)
        self.assertEqual(calls["amap.maps.direction.driving"].arguments["origin"], "深圳")
        self.assertEqual(calls["amap.maps.direction.driving"].arguments["destination"], "汕头")
        weather_cities = [
            call.arguments["city"]
            for call in plan.calls
            if call.tool_key == "amap.maps.weather"
        ]
        self.assertEqual(weather_cities, ["深圳", "汕头"])

    def test_deterministic_fallback_splits_multi_city_weather_query(self) -> None:
        plan = DeterministicToolPlanner().plan(query="深圳和广州天气怎么样", enabled=True)

        self.assertEqual([call.tool_key for call in plan.calls], ["amap.maps.weather", "amap.maps.weather"])
        self.assertEqual([call.arguments["city"] for call in plan.calls], ["深圳", "广州"])

    def test_llm_planner_falls_back_on_invalid_json(self) -> None:
        async def run_test() -> None:
            planner = LLMToolPlanner(chat_provider=FakeChatProvider("not-json"))

            plan = await planner.plan(
                query="美国现任总统是谁",
                enabled=True,
                runtime=PlannerRuntime(
                    provider_type="openai-compatible",
                    base_url="https://example.test/v1",
                    api_key="sk-test",
                    model_name="deepseek-ai/DeepSeek-V4-Flash",
                ),
            )

            self.assertEqual(plan.router, "deterministic_tool_planner_v2")
            self.assertEqual(plan.calls[0].tool_key, "web.tavily.search")
            event_types = [event["type"] for event in plan.trace_events]
            self.assertIn("tool_planner_start", event_types)
            self.assertIn("tool_fallback", event_types)
            self.assertIn("tool_schema_validation", event_types)
            self.assertIn("tool_planner_end", event_types)
            fallback_events = [event for event in plan.trace_events if event["type"] == "tool_fallback"]
            self.assertIn("LLM 工具规划失败", fallback_events[0]["reason"])

    def test_llm_planner_does_not_expose_provider_exception_text_in_fallback_trace(self) -> None:
        class FailingProvider:
            async def complete_chat(self, **_: object) -> str:
                raise RuntimeError("https://provider.internal/v1?api_key=secret-value")

        async def run_test() -> None:
            planner = LLMToolPlanner(chat_provider=FailingProvider())
            plan = await planner.plan(
                query="查一下最新信息",
                enabled=True,
                runtime=PlannerRuntime(
                    provider_type="openai-compatible",
                    base_url="https://example.test/v1",
                    api_key="test-key",
                    model_name="test-model",
                ),
            )
            fallback_events = [event for event in plan.trace_events if event["type"] == "tool_fallback"]
            self.assertEqual(len(fallback_events), 1)
            self.assertIn("LLM 工具规划失败", fallback_events[0]["reason"])
            self.assertNotIn("provider.internal", fallback_events[0]["reason"])
            self.assertNotIn("secret-value", fallback_events[0]["reason"])

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
