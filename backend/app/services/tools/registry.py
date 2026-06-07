from app.services.tools.catalog import ToolCatalog
from app.services.tools.schemas import ToolDefinition


class ToolRegistry(ToolCatalog):
    """Backward-compatible alias for the new ToolCatalog."""

    def web_search_tool(self) -> ToolDefinition:
        return self.get("web.tavily.search")

    def weather_tool(self) -> ToolDefinition:
        return self.get("amap.maps.weather")

    def map_tool(self) -> ToolDefinition:
        return self.get("amap.maps.text_search")
