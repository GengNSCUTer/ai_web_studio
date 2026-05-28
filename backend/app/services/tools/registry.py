from __future__ import annotations

from app.services.tools.schemas import ToolDefinition


class ToolRegistry:
    def __init__(self) -> None:
        self._definitions = {
            "web.tavily.search": ToolDefinition(
                tool_key="web.tavily.search",
                provider="tavily",
                category="web_search",
                display_name="Tavily 搜索",
                description="查询互联网最新网页资料、新闻、官网、版本、政策、价格等实时信息。",
            ),
            "amap.weather.current": ToolDefinition(
                tool_key="amap.weather.current",
                provider="amap",
                category="weather",
                display_name="高德天气",
                description="查询中国城市当前天气、温度、湿度、风向和发布时间。",
            ),
            "amap.map.basic": ToolDefinition(
                tool_key="amap.map.basic",
                provider="amap",
                category="map",
                display_name="高德地图",
                description="查询地址、地点、附近 POI、路线规划和行政区信息。",
            ),
        }

    def get(self, tool_key: str) -> ToolDefinition:
        return self._definitions[tool_key]

    def list_definitions(self) -> list[ToolDefinition]:
        return list(self._definitions.values())

    def web_search_tool(self) -> ToolDefinition:
        return self.get("web.tavily.search")

    def weather_tool(self) -> ToolDefinition:
        return self.get("amap.weather.current")

    def map_tool(self) -> ToolDefinition:
        return self.get("amap.map.basic")
