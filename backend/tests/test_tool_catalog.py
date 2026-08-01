from __future__ import annotations

import json
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.tool_config import McpServer, McpTool, WorkspaceToolSetting
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

    def test_planner_description_reinforces_external_evidence_and_scope(self) -> None:
        catalog = ToolCatalog()
        description = catalog.prompt_description(catalog.get("web.tavily.search"))

        self.assertIn("适用场景", description)
        self.assertIn("远程工具元数据（不可信", description)
        self.assertIn("来源：外部 MCP", description)
        self.assertIn("不可信 evidence", description)
        self.assertIn("只读", description)

    def test_planner_description_marks_write_tools_as_confirmation_required(self) -> None:
        definition = ToolDefinition(
            tool_key="workspace.files.apply_edit",
            provider="workspace",
            category="workspace_file",
            display_name="Apply edit",
            description="Create an approved file edit.",
            risk_level="high",
            read_only=False,
        )

        description = ToolCatalog.prompt_description(definition)
        self.assertIn("非只读/高风险", description)
        self.assertIn("用户确认", description)
        self.assertIn("当前用户/当前项目", description)

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

    def test_project_scoped_mcp_server_is_not_visible_to_other_projects(self) -> None:
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        Base.metadata.create_all(bind=engine)
        db = SessionLocal()
        try:
            scoped_server = McpServer(
                user_id="user-1",
                project_id="project-a",
                server_key="scoped_search",
                name="Scoped Search",
                url="https://example.test/mcp",
                auth_type="none",
                is_enabled=True,
            )
            db.add(scoped_server)
            db.flush()
            db.add(
                McpTool(
                    server_id=scoped_server.id,
                    raw_name="search",
                    tool_key="mcp.scoped.search",
                    display_name="Scoped Search",
                    description="Project-scoped search",
                    input_schema_json=json.dumps({"type": "object"}),
                    output_schema_json=json.dumps({}),
                    category="web_search",
                    risk_level="low",
                    read_only=True,
                    risk_reviewed=True,
                    is_enabled=True,
                )
            )
            db.commit()

            other_project = ToolCatalog(db=db, user_id="user-1", project_id="project-b")
            current_project = ToolCatalog(db=db, user_id="user-1", project_id="project-a")

            self.assertIsNone(other_project.get_or_none("mcp.scoped.search"))
            self.assertIsNotNone(current_project.get_or_none("mcp.scoped.search"))
        finally:
            db.close()
            Base.metadata.drop_all(bind=engine)
            engine.dispose()

    def test_workspace_disabled_tool_is_not_candidate_for_project_catalog(self) -> None:
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        Base.metadata.create_all(bind=engine)
        db = SessionLocal()
        try:
            db.add(WorkspaceToolSetting(project_id="project-a", tool_key="amap.maps.weather", is_enabled=False))
            db.commit()

            catalog = ToolCatalog(db=db, user_id="user-1", project_id="project-a")
            self.assertFalse(catalog.get("amap.maps.weather").enabled_by_default)
        finally:
            db.close()
            Base.metadata.drop_all(bind=engine)
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
