from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes.tools import (
    _dynamic_tool_key,
    _json_loads,
    review_mcp_tool_onboarding,
    submit_mcp_tool_onboarding,
    test_mcp_tool,
    update_mcp_tool,
    update_workspace_agent_policy,
)
from app.core.database import Base
from app.models.tool_config import McpServer, McpTool
from app.models.project import Project
from app.schemas.tool_config import (
    McpToolOnboardingReview,
    McpToolOnboardingSubmit,
    McpToolTestRequest,
    McpToolUpdate,
    WorkspaceAgentPolicyUpdate,
)
from app.services.tools.onboarding import (
    ONBOARDING_CONTRACT_VERSION,
    ONBOARDING_FIXTURE_FORMAT,
    fixture_bundle_digest,
)


def _onboarding_payload(*, tool: McpTool, server: McpServer) -> McpToolOnboardingSubmit:
    fixture_bundle = {
        "format": ONBOARDING_FIXTURE_FORMAT,
        "cases": [
            {
                "id": "success",
                "response": {
                    "result": {
                        "structuredContent": {
                            "items": [
                                {
                                    "snippet": "可引用的搜索正文",
                                    "href": "https://example.test/evidence",
                                }
                            ]
                        }
                    }
                },
                "expected": {"quality_status": "valid", "source_count": {"min": 1, "max": 1}},
            },
            {
                "id": "malformed",
                "response": {"result": {"structuredContent": {"items": [{"href": "https://example.test"}]}}},
                "expected": {"quality_status": "invalid", "source_count": {"min": 0, "max": 0}},
            },
        ],
    }
    return McpToolOnboardingSubmit(
        contract={
            "version": ONBOARDING_CONTRACT_VERSION,
            "tool": {
                "tool_key": tool.tool_key,
                "provider": server.server_key,
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
                "max_items": 4,
                "max_chars": 600,
            },
            "fixture_manifest": {
                "format": ONBOARDING_FIXTURE_FORMAT,
                "bundle_digest": fixture_bundle_digest(fixture_bundle),
                "case_ids": ["success", "malformed"],
            },
        },
        fixture_bundle=fixture_bundle,
    )


class ToolRoutesTest(unittest.TestCase):
    def test_json_loads_preserves_non_empty_mcp_configuration(self) -> None:
        self.assertEqual(_json_loads('{"type":"object"}', {}), {"type": "object"})
        self.assertEqual(_json_loads("not-json", {"fallback": True}), {"fallback": True})

    def test_dynamic_tool_key_is_bounded_and_server_scoped(self) -> None:
        first = _dynamic_tool_key(server_id="server-a", raw_name="search weather")
        second = _dynamic_tool_key(server_id="server-b", raw_name="search weather")
        collision = _dynamic_tool_key(server_id="server-a", raw_name="search-weather")

        self.assertNotEqual(first, second)
        self.assertNotEqual(first, collision)
        self.assertLessEqual(len(first), 128)

    def test_workspace_policy_update_is_owner_scoped(self) -> None:
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        Base.metadata.create_all(bind=engine)
        db = SessionLocal()
        try:
            owned = Project(user_id="user-1", name="Owned")
            foreign = Project(user_id="user-2", name="Foreign")
            db.add_all([owned, foreign])
            db.commit()

            response = update_workspace_agent_policy(
                project_id=owned.id,
                payload=WorkspaceAgentPolicyUpdate(permission_mode="full_workspace"),
                db=db,
                current_user=SimpleNamespace(id="user-1"),
            )
            self.assertEqual(response.permission_mode, "full_workspace")

            with self.assertRaises(HTTPException) as captured:
                update_workspace_agent_policy(
                    project_id=foreign.id,
                    payload=WorkspaceAgentPolicyUpdate(permission_mode="ask"),
                    db=db,
                    current_user=SimpleNamespace(id="user-1"),
                )
            self.assertEqual(captured.exception.status_code, 404)
        finally:
            db.close()
            engine.dispose()

    def test_unreviewed_mcp_tool_test_is_blocked_before_network(self) -> None:
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        Base.metadata.create_all(bind=engine)
        db = SessionLocal()
        try:
            server = McpServer(
                user_id="user-1",
                server_key="unsafe",
                name="Unsafe",
                url="https://example.test/mcp",
                auth_type="none",
                is_enabled=True,
            )
            db.add(server)
            db.flush()
            tool = McpTool(
                server_id=server.id,
                raw_name="write_data",
                tool_key="mcp.unsafe.write_data",
                display_name="Write data",
                risk_level="high",
                read_only=False,
                risk_reviewed=False,
                is_enabled=False,
            )
            db.add(tool)
            db.commit()

            with self.assertRaises(HTTPException) as captured:
                asyncio.run(
                    test_mcp_tool(
                        tool_id=tool.id,
                        payload=McpToolTestRequest(arguments={}),
                        db=db,
                        current_user=SimpleNamespace(id="user-1"),
                    )
                )

            self.assertEqual(captured.exception.status_code, 409)
        finally:
            db.close()
            engine.dispose()

    def test_onboarding_requires_fixture_then_explicit_review_before_enablement(self) -> None:
        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)
        Base.metadata.create_all(bind=engine)
        db = SessionLocal()
        try:
            server = McpServer(
                user_id="user-1",
                server_key="custom_search",
                name="Custom search",
                url="https://example.test/mcp",
                auth_type="none",
                is_enabled=True,
            )
            db.add(server)
            db.flush()
            tool = McpTool(
                server_id=server.id,
                raw_name="search",
                tool_key="mcp.custom.search",
                display_name="Search",
                description="Search web evidence",
                input_schema_json='{"type":"object"}',
                output_schema_json='{"type":"object"}',
                category="web_search",
                risk_level="low",
                read_only=True,
                risk_reviewed=True,
                is_enabled=False,
            )
            db.add(tool)
            db.commit()

            submitted = submit_mcp_tool_onboarding(
                tool_id=tool.id,
                payload=_onboarding_payload(tool=tool, server=server),
                db=db,
                current_user=SimpleNamespace(id="user-1"),
            )
            self.assertEqual(submitted["tool"]["onboarding_review_status"], "pending_review")
            self.assertFalse(submitted["tool"]["is_enabled"])
            self.assertTrue(submitted["report"]["valid"])
            self.assertNotIn("response", str(submitted))

            reviewed = review_mcp_tool_onboarding(
                tool_id=tool.id,
                payload=McpToolOnboardingReview(approved=True),
                db=db,
                current_user=SimpleNamespace(id="user-1"),
            )
            self.assertEqual(reviewed.onboarding_review_status, "approved")
            self.assertFalse(reviewed.is_enabled)

            enabled = update_mcp_tool(
                tool_id=tool.id,
                payload=McpToolUpdate(is_enabled=True),
                db=db,
                current_user=SimpleNamespace(id="user-1"),
            )
            self.assertTrue(enabled.is_enabled)

            changed = update_mcp_tool(
                tool_id=tool.id,
                payload=McpToolUpdate(description_override="Changed candidate description"),
                db=db,
                current_user=SimpleNamespace(id="user-1"),
            )
            self.assertEqual(changed.onboarding_review_status, "invalidated")
            self.assertFalse(changed.is_enabled)

            with self.assertRaises(HTTPException) as captured:
                update_mcp_tool(
                    tool_id=tool.id,
                    payload=McpToolUpdate(is_enabled=True),
                    db=db,
                    current_user=SimpleNamespace(id="user-1"),
                )
            self.assertEqual(captured.exception.status_code, 409)

            # 显示名同样会进入 Planner 的候选描述，不能在复审后静默替换。
            resubmitted = submit_mcp_tool_onboarding(
                tool_id=tool.id,
                payload=_onboarding_payload(tool=tool, server=server),
                db=db,
                current_user=SimpleNamespace(id="user-1"),
            )
            self.assertEqual(resubmitted["tool"]["onboarding_review_status"], "pending_review")
            review_mcp_tool_onboarding(
                tool_id=tool.id,
                payload=McpToolOnboardingReview(approved=True),
                db=db,
                current_user=SimpleNamespace(id="user-1"),
            )
            renamed = update_mcp_tool(
                tool_id=tool.id,
                payload=McpToolUpdate(display_name="Renamed candidate tool"),
                db=db,
                current_user=SimpleNamespace(id="user-1"),
            )
            self.assertEqual(renamed.onboarding_review_status, "invalidated")
            self.assertFalse(renamed.is_enabled)
        finally:
            db.close()
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
