from __future__ import annotations

import re
from uuid import uuid4

from app.services.tools.registry import ToolRegistry
from app.services.tools.schemas import PlannedToolCall, ToolPlan


class RuleBasedToolRouter:
    WEATHER_PATTERN = re.compile(r"(天气|气温|温度|下雨|降雨|台风|空气质量|冷不冷|热不热)")
    MAP_PATTERN = re.compile(r"(附近|路线|怎么去|怎么走|地址|导航|公交|驾车|开车|步行|地铁|周边|位置|在哪|哪里|地图|距离|行政区)")

    def __init__(self, registry: ToolRegistry | None = None) -> None:
        self.registry = registry or ToolRegistry()

    def plan(self, *, query: str, enabled: bool) -> ToolPlan:
        if not enabled:
            return ToolPlan(
                plan_id=str(uuid4()),
                router="rule_based_v1",
                external_context_allowed=False,
                should_use_tools=False,
                calls=[],
            )

        if self.WEATHER_PATTERN.search(query):
            definition = self.registry.weather_tool()
            reason = "用户问题命中天气相关关键词。"
            confidence = 0.88
        elif self.MAP_PATTERN.search(query):
            definition = self.registry.map_tool()
            reason = "用户问题命中地点、路线或地图相关关键词。"
            confidence = 0.86
        else:
            definition = self.registry.web_search_tool()
            reason = "用户开启联网搜索，且未命中专用天气/地图工具，使用网页搜索兜底。"
            confidence = 0.72

        return ToolPlan(
            plan_id=str(uuid4()),
            router="rule_based_v1",
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
                    arguments={"query": query},
                )
            ],
            fallback_tool_key="web.tavily.search" if definition.tool_key != "web.tavily.search" else None,
        )
