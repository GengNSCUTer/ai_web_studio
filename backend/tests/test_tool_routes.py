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
    test_mcp_tool,
    update_workspace_agent_policy,
)
from app.core.database import Base
from app.models.tool_config import McpServer, McpTool
from app.models.project import Project
from app.schemas.tool_config import McpToolTestRequest, WorkspaceAgentPolicyUpdate


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


if __name__ == "__main__":
    unittest.main()
