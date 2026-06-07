from __future__ import annotations

import re
from uuid import uuid4

from app.services.tools.catalog import ToolCatalog
from app.services.tools.registry import ToolRegistry
from app.services.tools.schemas import PlannedToolCall, ToolPlan


class ManifestToolPlanner:
    """Selects a small tool plan from the catalog.

    This is a compatibility planner, not the final Agent-style LLM planner. It
    keeps deterministic behavior while execution already moves to manifest
    schema + adapter dispatch.
    """

    WEATHER_PATTERN = re.compile(r"(天气|气温|温度|下雨|降雨|台风|空气质量|冷不冷|热不热)")
    MAP_PATTERN = re.compile(
        r"(附近|路线|怎么去|怎么走|地址|导航|公交|驾车|开车|步行|地铁|周边|位置|在哪|哪里|地图|距离|行政区|"
        r"多远|相距|离.+远|几公里|多少公里|开车多久|步行多久|要多久|多久到)"
    )

    def __init__(self, catalog: ToolCatalog | None = None) -> None:
        self.catalog = catalog or ToolRegistry()

    def plan(self, *, query: str, enabled: bool) -> ToolPlan:
        if not enabled:
            return ToolPlan(
                plan_id=str(uuid4()),
                router="manifest_planner_v1",
                external_context_allowed=False,
                should_use_tools=False,
                calls=[],
            )

        if self.WEATHER_PATTERN.search(query):
            definition = self.catalog.get("amap.maps.weather")
            reason = "用户问题命中天气相关意图，按 manifest 选择高德天气。"
            confidence = 0.88
        elif self.MAP_PATTERN.search(query):
            definition = self.catalog.get_or_none("amap.maps.text_search") or self.catalog.first_by_category("map_poi")
            reason = "用户问题命中地点、路线或地图相关意图，按 manifest 选择高德地图。"
            confidence = 0.86
        else:
            definition = self.catalog.first_by_category("web_search")
            reason = "用户开启联网搜索，且未命中专用天气/地图工具，使用网页搜索兜底。"
            confidence = 0.72
        arguments = {"query": query}

        return ToolPlan(
            plan_id=str(uuid4()),
            router="manifest_planner_v1",
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


class RuleBasedToolRouter(ManifestToolPlanner):
    """Compatibility alias. Prefer ManifestToolPlanner for new code."""
