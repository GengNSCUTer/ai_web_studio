from __future__ import annotations

import json
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.tool_config import McpServer, McpTool
from app.services.tools.catalog import ToolCatalog
from app.services.tools.adapters import ToolAdapterRunner
from app.services.tools.schemas import PlannedToolCall
from app.services.tools.schemas import ToolDefinition


class ToolCatalogTest(unittest.TestCase):
    def test_fixed_arguments_override_model_arguments(self) -> None:
        definition = ToolDefinition(
            tool_key="mcp.tenant.lookup",
            provider="test",
            category="web_search",
            display_name="Tenant lookup",
            description="Tenant lookup",
            adapter={
                "default_arguments": {"limit": 5},
                "fixed_arguments": {"tenant_id": "trusted"},
            },
        )
        call = PlannedToolCall(
            call_id="call-1",
            tool_key=definition.tool_key,
            provider=definition.provider,
            category=definition.category,
            display_name=definition.display_name,
            confidence=1.0,
            reason="test",
            arguments={"query": "hello", "tenant_id": "attacker", "limit": 10},
        )

        arguments = ToolAdapterRunner._build_adapter_arguments(definition=definition, call=call)

        self.assertEqual(arguments["tenant_id"], "trusted")
        self.assertEqual(arguments["limit"], 10)

    def test_loads_manifest_schema_and_adapter_metadata(self) -> None:
        catalog = ToolCatalog()

        tavily = catalog.get("web.tavily.search")
        weather = catalog.get("amap.maps.weather")
        geo = catalog.get("amap.maps.geo")
        route = catalog.get("amap.maps.direction.driving")
        distance = catalog.get("amap.maps.distance")
        poi = catalog.get("amap.maps.text_search")

        self.assertEqual(tavily.adapter_type, "mcp_http")
        self.assertEqual(tavily.source_type, "mcp")
        self.assertEqual(tavily.adapter["mcp_tool_name"], "tavily_search")
        self.assertIn("query", tavily.input_schema["required"])

        self.assertEqual(weather.adapter_type, "mcp_http")
        self.assertEqual(weather.credential_provider, "amap")
        self.assertEqual(weather.fallback_tool_key, "web.tavily.search")
        self.assertIn("city", weather.input_schema["required"])
        self.assertEqual(weather.adapter["mcp_tool_name"], "maps_weather")

        self.assertEqual(geo.adapter["mcp_tool_name"], "maps_geo")
        self.assertEqual(route.adapter["mcp_tool_name"], "maps_direction_driving")
        self.assertEqual(distance.adapter["mcp_tool_name"], "maps_distance")
        self.assertEqual(poi.adapter["mcp_tool_name"], "maps_text_search")
        self.assertEqual(catalog.first_by_category("map_poi").tool_key, "amap.maps.text_search")

    def test_loads_enabled_mcp_tools_from_database(self) -> None:
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        Base.metadata.create_all(bind=engine)
        db = SessionLocal()
        try:
            server = McpServer(
                user_id="user-1",
                server_key="custom_search",
                name="Custom Search",
                description="User registered search MCP server",
                transport_type="streamable_http",
                url="https://example.test/mcp?key={api_key}",
                auth_type="api_key",
                credential_provider="custom_search_key",
                is_enabled=True,
            )
            db.add(server)
            db.flush()
            db.add_all(
                [
                    McpTool(
                        server_id=server.id,
                        raw_name="search",
                        tool_key="mcp.custom_search.search",
                        display_name="Search",
                        description="Search public web pages",
                        input_schema_json=json.dumps(
                            {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
                        ),
                        output_schema_json=json.dumps(
                            {"type": "object", "properties": {"items": {"type": "array"}}, "required": ["items"]}
                        ),
                        fixed_arguments_json=json.dumps({"limit": 5}),
                        category="web_search",
                        risk_level="low",
                        read_only=True,
                        risk_reviewed=True,
                        is_enabled=True,
                    ),
                    McpTool(
                        server_id=server.id,
                        raw_name="disabled_search",
                        tool_key="mcp.custom_search.disabled_search",
                        display_name="Disabled Search",
                        description="Disabled tool",
                        category="web_search",
                        risk_level="low",
                        read_only=True,
                        risk_reviewed=True,
                        is_enabled=False,
                    ),
                    McpTool(
                        server_id=server.id,
                        raw_name="unreviewed_search",
                        tool_key="mcp.custom_search.unreviewed_search",
                        display_name="Unreviewed Search",
                        description="Enabled flag must not bypass risk review",
                        category="web_search",
                        risk_level="low",
                        read_only=True,
                        risk_reviewed=False,
                        is_enabled=True,
                    ),
                ]
            )
            db.commit()

            catalog = ToolCatalog(db=db, user_id="user-1")
            definition = catalog.get("mcp.custom_search.search")

            self.assertIsNone(catalog.get_or_none("mcp.custom_search.disabled_search"))
            self.assertIsNone(catalog.get_or_none("mcp.custom_search.unreviewed_search"))
            self.assertEqual(definition.source_type, "mcp_server")
            self.assertEqual(definition.adapter_type, "mcp_http")
            self.assertEqual(definition.provider, "custom_search")
            self.assertEqual(definition.credential_provider, "custom_search_key")
            self.assertEqual(definition.adapter["endpoint_template"], "https://example.test/mcp?key={api_key}")
            self.assertEqual(definition.adapter["mcp_tool_name"], "search")
            self.assertEqual(definition.adapter["auth_type"], "api_key")
            self.assertEqual(definition.adapter["fixed_arguments"], {"limit": 5})
            self.assertEqual(definition.input_schema["required"], ["query"])
            self.assertEqual(definition.output_schema["required"], ["items"])
            self.assertTrue(definition.read_only)
        finally:
            db.close()
            Base.metadata.drop_all(bind=engine)
            engine.dispose()

    def test_no_auth_mcp_definition_does_not_require_credential(self) -> None:
        definition = ToolDefinition(
            tool_key="mcp.public.weather",
            provider="public",
            category="weather",
            display_name="Public Weather",
            description="No-auth public weather tool",
            adapter_type="mcp_http",
            adapter={
                "endpoint_template": "https://example.test/mcp",
                "mcp_tool_name": "weather",
                "auth_type": "none",
            },
        )

        self.assertFalse(definition.credential_required)


if __name__ == "__main__":
    unittest.main()
