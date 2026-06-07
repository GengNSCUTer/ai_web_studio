from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from app.services.chat_provider_service import ChatProviderService
from app.services.tools.catalog import ToolCatalog
from app.services.tools.providers.amap import AmapToolProvider
from app.services.tools.router import ManifestToolPlanner
from app.services.tools.schemas import PlannedToolCall, ToolPlan
from app.services.tools.selector import ToolCandidateSelector
from app.services.tools.validation import ToolSchemaValidationError, ToolSchemaValidator


@dataclass(frozen=True)
class PlannerRuntime:
    provider_type: str
    base_url: str
    api_key: str | None
    model_name: str


class LLMToolPlanner:
    planner_timeout_seconds = 15

    def __init__(
        self,
        *,
        catalog: ToolCatalog | None = None,
        chat_provider: ChatProviderService | None = None,
        validator: ToolSchemaValidator | None = None,
        fallback_planner: ManifestToolPlanner | None = None,
        candidate_selector: ToolCandidateSelector | None = None,
    ) -> None:
        self.catalog = catalog or ToolCatalog()
        self.chat_provider = chat_provider or ChatProviderService()
        self.validator = validator or ToolSchemaValidator()
        self.fallback_planner = fallback_planner or DeterministicToolPlanner(self.catalog)
        self.candidate_selector = candidate_selector or ToolCandidateSelector(self.catalog)

    async def plan(
        self,
        *,
        query: str,
        enabled: bool,
        runtime: PlannerRuntime | None,
        recent_messages: list[object] | None = None,
        observations: list[dict[str, Any]] | None = None,
    ) -> ToolPlan:
        candidate_tools, candidate_trace = self.candidate_selector.select(query=query, enabled=enabled)
        start_event = {
            "type": "tool_planner_start",
            "planner": "llm_tool_planner_v1",
            "enabled": enabled,
            "query_preview": query[:240],
            "available_tools_count": len(candidate_tools),
            "runtime_provider": runtime.provider_type if runtime else None,
            "runtime_model": runtime.model_name if runtime else None,
        }
        if not enabled:
            plan = self.fallback_planner.plan(query=query, enabled=False)
            plan.trace_events = [
                start_event,
                candidate_trace,
                {
                    "type": "tool_planner_end",
                    "planner": plan.router,
                    "strategy": "disabled",
                    "should_use_tools": False,
                    "selected_tools": [],
                    "reason": "用户未启用外部工具。",
                },
            ]
            return plan
        deterministic_plan = self.fallback_planner.plan(query=query, enabled=True)
        if not runtime:
            self._attach_deterministic_trace(
                plan=deterministic_plan,
                start_event=start_event,
                candidate_trace=candidate_trace,
                strategy="fallback",
                fallback_reason="缺少可用于 LLM 工具规划的模型运行时配置。",
            )
            return deterministic_plan

        fallback_reason = ""
        try:
            messages = self._build_planner_messages(
                query=query,
                recent_messages=recent_messages,
                candidate_tools=candidate_tools,
                observations=observations or [],
            )
            text = await asyncio.wait_for(
                self.chat_provider.complete_chat(
                    provider_type=runtime.provider_type,
                    base_url=runtime.base_url,
                    api_key=runtime.api_key,
                    model_name=runtime.model_name,
                    messages=messages,
                    temperature=0.0,
                    top_p=0.1,
                    max_tokens=1800,
                ),
                timeout=self.planner_timeout_seconds,
            )
            plan = self._parse_llm_plan(text=text, query=query, allowed_tool_keys={tool.tool_key for tool in candidate_tools})
            if not plan.should_use_tools:
                plan.trace_events.insert(0, start_event)
                plan.trace_events.insert(1, candidate_trace)
                return plan
            if plan.calls:
                plan.trace_events.insert(0, start_event)
                plan.trace_events.insert(1, candidate_trace)
                plan.trace_events.append(
                    {
                        "type": "tool_planner_end",
                        "planner": plan.router,
                        "strategy": "llm_primary",
                        "should_use_tools": plan.should_use_tools,
                        "selected_tools": [call.to_public_dict() for call in plan.calls],
                        "reason": "LLM 根据候选工具、工具 manifest 和 schema 生成工具计划。",
                    }
                )
                return plan
            fallback_reason = "LLM 工具规划未返回可执行工具调用。"
        except asyncio.TimeoutError:
            fallback_reason = f"LLM 工具规划超过 {self.planner_timeout_seconds} 秒。"
        except Exception as exc:
            fallback_reason = f"LLM 工具规划失败：{exc}"
        self._attach_deterministic_trace(
            plan=deterministic_plan,
            start_event=start_event,
            candidate_trace=candidate_trace,
            strategy="fallback",
            fallback_reason=fallback_reason,
        )
        return deterministic_plan

    def _build_planner_messages(
        self,
        *,
        query: str,
        recent_messages: list[object] | None,
        candidate_tools: list[Any],
        observations: list[dict[str, Any]],
    ) -> list[dict[str, str]]:
        tools = [
            {
                "tool_key": tool.tool_key,
                "provider": tool.provider,
                "category": tool.category,
                "display_name": tool.display_name,
                "description": tool.description,
                "when_to_use": tool.when_to_use,
                "when_not_to_use": tool.when_not_to_use,
                "input_schema": tool.input_schema,
                "risk_level": tool.risk_level,
                "read_only": tool.read_only,
            }
            for tool in candidate_tools
        ]
        history = self._format_recent_messages(recent_messages or [])
        system = (
            "你是 AI Web Studio 的工具规划器。你只输出 JSON，不回答用户问题。\n"
            "目标：根据用户问题和候选工具，选择 0 到 5 个工具调用，并为每个工具生成结构化 arguments。\n"
            "规则：\n"
            "1. 只使用工具列表中存在的 tool_key。\n"
            "2. arguments 必须符合对应 input_schema。\n"
            "3. 天气优先用 amap.maps.weather。\n"
            "4. 驾车路线/开车多久用 amap.maps.direction.driving；步行用 amap.maps.direction.walking；公交地铁用 amap.maps.direction.transit。\n"
            "5. 多远/相距/哪个近用 amap.maps.distance。\n"
            "6. 地址解析用 amap.maps.geo；地点/附近/周边/服务区优先用 amap.maps.text_search 或 amap.maps.around_search。\n"
            "7. 最新网页事实、新闻、政策、版本、人物现任信息用 web.tavily.search。\n"
            "8. 沿途服务区、路上有哪些地点这类问题，优先同时调用 web.tavily.search 搜索完整问题，可补充高德地点搜索。\n"
            "9. 高德 MCP 路线/距离 schema 要求经纬度；如果用户只给地点名，可以先填地点名，执行器会在调用 MCP 前自动地理编码为坐标。\n"
            "10. 一个复杂问题可以拆成多个工具调用，例如路线 + 天气 + 网页搜索；用户问预计耗时时必须包含路线工具。\n"
            "11. 没有依赖关系的调用 depends_on=[] 且 can_parallel=true，执行器会并行执行；依赖上游结果的调用必须写 depends_on 且 can_parallel=false。\n"
            "12. 如果已有观察结果仍不足以回答，可设置 need_more_rounds=true，让系统下一轮继续规划。\n"
            "13. 如果工具是高风险或非只读，也可以规划，但必须在 reason 中说明为什么需要。\n"
            "14. 如果不需要工具，返回 should_use_tools=false。\n"
            "15. 不要因为问题复杂就只调用网页搜索；能用结构化工具查天气、路线、距离、地点时，必须把结构化工具也列入计划。\n"
            "示例 A：用户问“深圳和广州天气怎么样”，输出两个 amap.maps.weather 调用，分别 city=深圳、city=广州，depends_on=[]。\n"
            "示例 B：用户问“深圳到汕头路上有哪些服务区，顺便看天气和预计耗时”，输出驾车路线、深圳天气、汕头天气、服务区/地点搜索、网页搜索；路线和天气可并行，依赖路线结果再继续精查时设置 need_more_rounds=true。\n"
            "输出格式：{\"should_use_tools\": true, \"need_more_rounds\": false, \"calls\": [{\"id\":\"call_1\", \"tool_key\": \"...\", \"confidence\": 0.0-1.0, \"reason\": \"...\", \"depends_on\": [], \"can_parallel\": true, \"arguments\": {...}}]}\n"
        )
        observation_text = json.dumps(observations, ensure_ascii=False) if observations else "无"
        user = (
            f"【最近上下文】\n{history or '无'}\n\n"
            f"【用户问题】\n{query}\n\n"
            f"【已经获得的工具观察结果】\n{observation_text}\n\n"
            f"【可用工具 JSON】\n{json.dumps(tools, ensure_ascii=False)}"
        )
        return [{"role": "system", "content": system}, {"role": "user", "content": user}]

    def _parse_llm_plan(self, *, text: str, query: str, allowed_tool_keys: set[str] | None = None) -> ToolPlan:
        data = self._loads_json_object(text)
        calls: list[PlannedToolCall] = []
        trace_events: list[dict[str, Any]] = [
            {
                "type": "tool_planner_llm_output",
                "planner": "llm_tool_planner_v1",
                "raw_preview": text[:1200],
            }
        ]
        if not bool(data.get("should_use_tools")):
            return ToolPlan(
                plan_id=str(uuid4()),
                router="llm_tool_planner_v1",
                external_context_allowed=True,
                should_use_tools=False,
                calls=[],
                trace_events=[
                    *trace_events,
                    {
                        "type": "tool_planner_end",
                        "planner": "llm_tool_planner_v1",
                        "strategy": "llm",
                        "should_use_tools": False,
                        "selected_tools": [],
                        "reason": "LLM 判断本轮不需要外部工具。",
                    },
                ],
            )

        for item in (data.get("calls") or [])[:5]:
            if not isinstance(item, dict):
                trace_events.append(
                    {
                        "type": "tool_schema_validation",
                        "planner": "llm_tool_planner_v1",
                        "status": "failed",
                        "error": "tool call 不是 JSON object。",
                        "raw_arguments": item,
                    }
                )
                continue
            tool_key = str(item.get("tool_key") or "")
            definition = self.catalog.get_or_none(tool_key)
            if not definition or (allowed_tool_keys is not None and tool_key not in allowed_tool_keys):
                trace_events.append(
                    {
                        "type": "tool_schema_validation",
                        "planner": "llm_tool_planner_v1",
                        "tool_key": tool_key,
                        "status": "failed",
                        "error": "工具不存在于本轮候选工具集合。",
                    }
                )
                continue
            arguments = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
            raw_arguments = dict(arguments)
            arguments.setdefault("query", query)
            try:
                arguments = self.validator.validate(definition=definition, arguments=arguments)
            except ToolSchemaValidationError as exc:
                trace_events.append(
                    {
                        "type": "tool_schema_validation",
                        "planner": "llm_tool_planner_v1",
                        "tool_key": definition.tool_key,
                        "display_name": definition.display_name,
                        "status": "failed",
                        "raw_arguments": raw_arguments,
                        "error": str(exc),
                    }
                )
                continue
            trace_events.append(
                {
                    "type": "tool_schema_validation",
                    "planner": "llm_tool_planner_v1",
                    "tool_key": definition.tool_key,
                    "display_name": definition.display_name,
                    "status": "passed",
                    "raw_arguments": raw_arguments,
                    "normalized_arguments": arguments,
                    "required": definition.input_schema.get("required") or [],
                }
            )
            call_id = str(item.get("id") or item.get("call_id") or uuid4())
            raw_depends_on = item.get("depends_on") if isinstance(item.get("depends_on"), list) else []
            calls.append(
                PlannedToolCall(
                    call_id=call_id,
                    tool_key=definition.tool_key,
                    provider=definition.provider,
                    category=definition.category,
                    display_name=definition.display_name,
                    confidence=float(item.get("confidence") or 0.75),
                    reason=str(item.get("reason") or "LLM 根据工具 manifest 选择。"),
                    arguments=arguments,
                    depends_on=[str(dep) for dep in raw_depends_on if str(dep).strip()],
                    can_parallel=bool(item.get("can_parallel", True)),
                )
            )

        known_ids = {call.call_id for call in calls}
        for call in calls:
            call.depends_on = [dep for dep in call.depends_on if dep in known_ids and dep != call.call_id]

        return ToolPlan(
            plan_id=str(uuid4()),
            router="llm_tool_planner_v1",
            external_context_allowed=True,
            should_use_tools=bool(data.get("should_use_tools")),
            calls=calls,
            fallback_tool_key="web.tavily.search",
            need_more_rounds=bool(data.get("need_more_rounds") or data.get("should_continue")),
            trace_events=trace_events,
        )

    def _attach_deterministic_trace(
        self,
        *,
        plan: ToolPlan,
        start_event: dict[str, Any],
        candidate_trace: dict[str, Any],
        strategy: str,
        fallback_reason: str | None,
    ) -> None:
        events = [start_event, candidate_trace]
        if fallback_reason:
            events.append(
                {
                    "type": "tool_fallback",
                    "from": "llm_tool_planner_v1",
                    "to": plan.router,
                    "reason": fallback_reason,
                }
            )
        for call in plan.calls:
            definition = self.catalog.get_or_none(call.tool_key)
            events.append(
                {
                    "type": "tool_schema_validation",
                    "planner": plan.router,
                    "tool_key": call.tool_key,
                    "display_name": call.display_name,
                    "status": "passed" if definition else "skipped",
                    "raw_arguments": call.arguments,
                    "normalized_arguments": call.arguments,
                    "required": (definition.input_schema.get("required") or []) if definition else [],
                    "reason": "确定性规划器生成的参数已按工具 manifest 结构使用。",
                }
            )
        events.append(
            {
                "type": "tool_planner_end",
                "planner": plan.router,
                "strategy": strategy,
                "should_use_tools": plan.should_use_tools,
                "selected_tools": [call.to_public_dict() for call in plan.calls],
                "reason": "高置信规则路径直接生成工具计划。" if strategy == "deterministic_shortcut" else "使用确定性规划器作为兜底。",
            }
        )
        plan.trace_events = events

    @staticmethod
    def _loads_json_object(text: str) -> dict[str, Any]:
        stripped = (text or "").strip()
        if stripped.startswith("```"):
            stripped = re.sub(r"^```(?:json)?", "", stripped).strip()
            stripped = re.sub(r"```$", "", stripped).strip()
        if not stripped.startswith("{"):
            match = re.search(r"\{.*\}", stripped, flags=re.S)
            if match:
                stripped = match.group(0)
        data = json.loads(stripped)
        if not isinstance(data, dict):
            raise ValueError("LLM planner output must be a JSON object.")
        return data

    @staticmethod
    def _format_recent_messages(messages: list[object]) -> str:
        lines: list[str] = []
        for message in messages[-8:]:
            role = getattr(message, "role", None)
            content = getattr(message, "content", None)
            if isinstance(message, dict):
                role = message.get("role", role)
                content = message.get("content", content)
            text = " ".join(str(content or "").split()).strip()
            if text:
                lines.append(f"{role or 'unknown'}: {text[:400]}")
        return "\n".join(lines)


class DeterministicToolPlanner(ManifestToolPlanner):
    def plan(self, *, query: str, enabled: bool) -> ToolPlan:
        if not enabled:
            return super().plan(query=query, enabled=False)

        multi_plan = self._build_multi_intent_plan(query)
        if multi_plan:
            return multi_plan

        route = AmapToolProvider._extract_route_query(query)
        if route:
            origin, destination, mode = route
            is_distance = bool(re.search(r"(多远|相距|距离|几公里|多少公里|哪个近|更近)", query)) and not bool(
                re.search(r"(开车多久|步行多久|要多久|多久到|怎么走|怎么去|路线|导航)", query)
            )
            if is_distance:
                definition = self.catalog.get("amap.maps.distance")
            elif mode == "walking":
                definition = self.catalog.get("amap.maps.direction.walking")
            elif mode == "transit":
                definition = self.catalog.get("amap.maps.direction.transit")
            else:
                definition = self.catalog.get("amap.maps.direction.driving")
            arguments: dict[str, Any]
            if definition.tool_key == "amap.maps.distance":
                origins = self._split_origin_candidates(origin)
                distance_type = {"straight": "0", "driving": "1", "walking": "3"}.get(mode, "1")
                arguments = {"origins": origins or [origin], "destination": destination, "type": distance_type, "query": query}
            else:
                arguments = {"origin": origin, "destination": destination, "query": query}
            return self._single_call_plan(
                query=query,
                definition=definition,
                confidence=0.88,
                reason="确定性规划器识别到路线或距离意图。",
                arguments=arguments,
            )

        if self.WEATHER_PATTERN.search(query):
            definition = self.catalog.get("amap.maps.weather")
            return self._single_call_plan(
                query=query,
                definition=definition,
                confidence=0.88,
                reason="确定性规划器识别到天气意图。",
                arguments={"city": AmapToolProvider._extract_city(query), "query": query},
            )

        if self.MAP_PATTERN.search(query):
            definition = self.catalog.get("amap.maps.text_search")
            keyword = AmapToolProvider._extract_map_keyword(query) or query
            return self._single_call_plan(
                query=query,
                definition=definition,
                confidence=0.78,
                reason="确定性规划器识别到地点、地址或周边搜索意图。",
                arguments={"keywords": keyword, "query": query},
            )

        definition = self.catalog.get("web.tavily.search")
        return self._single_call_plan(
            query=query,
            definition=definition,
            confidence=0.72,
            reason="默认使用网页搜索兜底。",
            arguments={"query": query},
        )

    def _build_multi_intent_plan(self, query: str) -> ToolPlan | None:
        compact = re.sub(r"\s+", "", query.strip())
        calls: list[PlannedToolCall] = []

        route = AmapToolProvider._extract_route_query(query)
        has_route_intent = bool(
            re.search(r"(路线|导航|怎么走|怎么去|开车|驾车|预计耗时|耗时|多久到|开车多久|路上|沿途|途中)", compact)
        )
        if route and has_route_intent:
            origin, destination, mode = route
            route_tool_key = {
                "walking": "amap.maps.direction.walking",
                "transit": "amap.maps.direction.transit",
                "driving": "amap.maps.direction.driving",
            }.get(mode, "amap.maps.direction.driving")
            definition = self.catalog.get(route_tool_key)
            calls.append(
                self._build_call(
                    definition=definition,
                    confidence=0.86,
                    reason="兜底规划器识别到路线/预计耗时意图，调用高德路线工具。",
                    arguments={"origin": origin, "destination": destination, "query": query},
                )
            )

            if self.WEATHER_PATTERN.search(query):
                for city in self._route_weather_cities(origin=origin, destination=destination):
                    definition = self.catalog.get("amap.maps.weather")
                    calls.append(
                        self._build_call(
                            definition=definition,
                            confidence=0.78,
                            reason="兜底规划器识别到路线问题中附带天气意图，查询起终点天气。",
                            arguments={"city": city, "query": query},
                        )
                    )

            if re.search(r"(服务区|加油站|充电站|休息区|路上|沿途|途中)", compact):
                definition = self.catalog.get("amap.maps.text_search")
                calls.append(
                    self._build_call(
                        definition=definition,
                        confidence=0.72,
                        reason="兜底规划器识别到沿途地点/服务区意图，调用高德关键词搜索。",
                        arguments={
                            "keywords": f"{origin}到{destination} 服务区",
                            "query": query,
                        },
                    )
                )
                web_definition = self.catalog.get("web.tav_summary") if self.catalog.get_or_none("web.tav_summary") else None
                web_definition = web_definition or self.catalog.get("web.tavily.search")
                calls.append(
                    self._build_call(
                        definition=web_definition,
                        confidence=0.7,
                        reason="沿途服务区信息可能需要网页资料补充，调用网页搜索兜底。",
                        arguments={"query": query},
                    )
                )

        elif self.WEATHER_PATTERN.search(query):
            cities = self._extract_weather_cities(query)
            if len(cities) >= 2:
                definition = self.catalog.get("amap.maps.weather")
                for city in cities[:3]:
                    calls.append(
                        self._build_call(
                            definition=definition,
                            confidence=0.82,
                            reason="兜底规划器识别到多个城市天气查询，拆分为多个天气工具调用。",
                            arguments={"city": city, "query": query},
                        )
                    )

        if len(calls) <= 1:
            return None
        calls = calls[:5]
        return ToolPlan(
            plan_id=str(uuid4()),
            router="deterministic_tool_planner_v2",
            external_context_allowed=True,
            should_use_tools=True,
            calls=calls,
            fallback_tool_key="web.tavily.search",
        )

    @staticmethod
    def _build_call(
        *,
        definition: Any,
        confidence: float,
        reason: str,
        arguments: dict[str, Any],
    ) -> PlannedToolCall:
        return PlannedToolCall(
            call_id=str(uuid4()),
            tool_key=definition.tool_key,
            provider=definition.provider,
            category=definition.category,
            display_name=definition.display_name,
            confidence=confidence,
            reason=reason,
            arguments=arguments,
        )

    @staticmethod
    def _route_weather_cities(*, origin: str, destination: str) -> list[str]:
        cities: list[str] = []
        for value in (origin, destination):
            city = AmapToolProvider._extract_city_name_from_place(value)
            if city and city not in cities:
                cities.append(city)
        return cities

    @staticmethod
    def _extract_weather_cities(query: str) -> list[str]:
        compact = re.sub(r"\s+", "", query.strip())
        prefix = re.split(r"(?:天气|气温|温度|下雨|降雨|空气质量)", compact, maxsplit=1)[0]
        prefix = re.sub(r"(今天|明天|后天|现在|当前|最近|请问|帮我|查一下|查询|分别|各自|怎么样|如何)", "", prefix)
        parts = re.split(r"(?:以及|和|与|跟|及|、|,|，|/|\\|\|)", prefix)
        cities: list[str] = []
        for part in parts:
            city = AmapToolProvider._extract_city_name_from_place(part)
            if city and city not in cities:
                cities.append(city)
        return cities

    @staticmethod
    def _split_origin_candidates(origin: str) -> list[str]:
        normalized = re.sub(r"(分别|各自|两者|二者)$", "", origin.strip())
        parts = re.split(r"(?:和|与|跟|及|以及|、|,|，|\|)", normalized)
        candidates: list[str] = []
        for part in parts:
            cleaned = AmapToolProvider._clean_place_text(part)
            cleaned = re.sub(r"(分别|各自|两者|二者)$", "", cleaned).strip()
            if cleaned and cleaned not in candidates:
                candidates.append(cleaned)
        return candidates

    @staticmethod
    def _single_call_plan(
        *,
        query: str,
        definition: Any,
        confidence: float,
        reason: str,
        arguments: dict[str, Any],
    ) -> ToolPlan:
        return ToolPlan(
            plan_id=str(uuid4()),
            router="deterministic_tool_planner_v2",
            external_context_allowed=True,
            should_use_tools=True,
            calls=[
                PlannedToolCall(
                    call_id=str(uuid4()),
                    tool_key=definition.tool_key,
                    provider=definition.provider,
                    category=definition.category,
                    display_name=definition.display_name,
                    confidence=confidence,
                    reason=reason,
                    arguments=arguments,
                )
            ],
            fallback_tool_key=definition.fallback_tool_key,
        )
