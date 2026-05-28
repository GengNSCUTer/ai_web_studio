from __future__ import annotations

import json
from pathlib import Path

from app.services.tools.schemas import ToolDefinition


class ToolRegistry:
    def __init__(self) -> None:
        manifest_path = Path(__file__).resolve().parents[2] / "tool_manifests" / "default_tools.json"
        records = json.loads(manifest_path.read_text(encoding="utf-8"))
        self._definitions = {
            record["tool_key"]: ToolDefinition(
                tool_key=record["tool_key"],
                provider=record["provider"],
                category=record["category"],
                display_name=record["display_name"],
                description=record["description"],
                enabled_by_default=bool(record.get("enabled_by_default", True)),
                read_only=bool(record.get("read_only", True)),
            )
            for record in records
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
