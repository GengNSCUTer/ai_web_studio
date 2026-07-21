from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes.tools import _dynamic_tool_key, test_mcp_tool
from app.core.database import Base
from app.models.tool_config import McpServer, McpTool
from app.schemas.tool_config import McpToolTestRequest


class ToolRoutesTest(unittest.TestCase):
    def test_dynamic_tool_key_is_bounded_and_server_scoped(self) -> None:
        first = _dynamic_tool_key(server_id="server-a", raw_name="search weather")
        second = _dynamic_tool_key(server_id="server-b", raw_name="search weather")
        collision = _dynamic_tool_key(server_id="server-a", raw_name="search-weather")

        self.assertNotEqual(first, second)
        self.assertNotEqual(first, collision)
        self.assertLessEqual(len(first), 128)

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
