from __future__ import annotations

import json
import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.tool_config import McpServer, McpTool, WorkspaceToolSetting
from app.services.tools.catalog import ToolCatalog
from app.services.tools.adapters import ToolAdapterRunner
from app.services.tools.onboarding import (
    ONBOARDING_CONTRACT_VERSION,
    ONBOARDING_FIXTURE_FORMAT,
    build_mcp_tool_config_digest,
    validate_tool_onboarding_contract,
)
from app.services.tools.schemas import PlannedToolCall
from app.services.tools.schemas import ToolDefinition


def _current_mcp_config_digest(*, tool: McpTool, server: McpServer) -> str:
    """让测试构造与生产 Catalog 相同的当前配置摘要。"""

    return build_mcp_tool_config_digest(
        tool_key=tool.tool_key,
        raw_name=tool.raw_name,
        display_name=tool.display_name,
        description=tool.description,
        description_override=tool.description_override,
        input_schema_json=tool.input_schema_json,
        output_schema_json=tool.output_schema_json,
        annotations_json=tool.annotations_json,
        fixed_arguments_json=tool.fixed_arguments_json,
        category=tool.category,
        risk_level=tool.risk_level,
        read_only=tool.read_only,
        server_key=server.server_key,
        server_url=server.url,
        server_transport_type=server.transport_type,
        server_auth_type=server.auth_type,
        credential_provider=server.credential_provider,
        server_project_id=server.project_id,
    )


def _approve_dynamic_mcp_tool(*, tool: McpTool, server: McpServer) -> None:
    """构造已通过 2.3D 摘要绑定的测试 Tool，不依赖真实网络或 fixture body。"""

    contract = validate_tool_onboarding_contract(
        {
            "version": ONBOARDING_CONTRACT_VERSION,
            "tool": {
                "tool_key": tool.tool_key,
                "provider": server.server_key[:64],
                "category": tool.category,
                "adapter_type": "mcp_http",
                "source_type": "mcp_server",
                "risk_level": tool.risk_level,
                "read_only": tool.read_only,
            },
            "quality_contract": {
                "semantic_profile": "web_search",
                "profile_mapping": {
                    "evidence_paths": ["/sources/*/metadata/raw/content"],
                    "identity_paths": ["/sources/*/metadata/raw/url"],
                    "collection_paths": ["/sources"],
                    "item_evidence_paths": ["/metadata/raw/content"],
                    "item_identity_paths": ["/metadata/raw/url"],
                },
            },
            "canonical_mapper": {
                "type": "collection",
                "collection_path": "/items",
                "display_text_path": "/snippet",
                "url_path": "/href",
                "canonical_fields": {"content": "/snippet", "url": "/href"},
                "max_items": 8,
                "max_chars": 1200,
            },
            "fixture_manifest": {
                "format": ONBOARDING_FIXTURE_FORMAT,
                "bundle_digest": "a" * 64,
                "case_ids": ["success", "malformed"],
            },
        }
    )
    tool.onboarding_contract_json = json.dumps(contract.to_dict(), ensure_ascii=False)
    tool.onboarding_contract_digest = contract.digest
    tool.onboarding_fixture_digest = contract.fixture_manifest["bundle_digest"]
    tool.onboarding_config_digest = _current_mcp_config_digest(tool=tool, server=server)
    tool.onboarding_review_status = "approved"


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
            approved_tool = McpTool(
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
            )
            _approve_dynamic_mcp_tool(tool=approved_tool, server=server)
            db.add_all(
                [
                    approved_tool,
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
            self.assertEqual(definition.quality_contract["semantic_profile"], "web_search")
            self.assertEqual(definition.adapter["result_mapper"], "declared_canonical")

            # 即使旧记录仍保留 risk_reviewed + is_enabled，候选目录也必须按当前
            # 描述/配置重算摘要；漂移后不向 Planner 暴露该 Tool。
            approved_tool.description_override = "Changed after onboarding review"
            db.commit()
            drifted_catalog = ToolCatalog(db=db, user_id="user-1")
            self.assertIsNone(drifted_catalog.get_or_none("mcp.custom_search.search"))

            # 即使绕过 API 直接改变访问协议或项目作用域，Catalog 仍会重算同一
            # 摘要并将旧审核 Tool 排除；不能只依赖 API 更新时的显式失效。
            approved_tool.description_override = None
            approved_tool.onboarding_config_digest = _current_mcp_config_digest(
                tool=approved_tool,
                server=server,
            )
            server.transport_type = "unexpected_transport"
            db.commit()
            protocol_drift_catalog = ToolCatalog(db=db, user_id="user-1")
            self.assertIsNone(protocol_drift_catalog.get_or_none("mcp.custom_search.search"))

            server.transport_type = "streamable_http"
            approved_tool.onboarding_config_digest = _current_mcp_config_digest(
                tool=approved_tool,
                server=server,
            )
            server.project_id = "different-project"
            db.commit()
            scope_drift_catalog = ToolCatalog(db=db, user_id="user-1")
            self.assertIsNone(scope_drift_catalog.get_or_none("mcp.custom_search.search"))

            server.project_id = None
            approved_tool.onboarding_config_digest = _current_mcp_config_digest(
                tool=approved_tool,
                server=server,
            )
            approved_tool.display_name = "Renamed after onboarding"
            db.commit()
            display_drift_catalog = ToolCatalog(db=db, user_id="user-1")
            self.assertIsNone(display_drift_catalog.get_or_none("mcp.custom_search.search"))
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
            scoped_tool = McpTool(
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
            _approve_dynamic_mcp_tool(tool=scoped_tool, server=scoped_server)
            db.add(scoped_tool)
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
