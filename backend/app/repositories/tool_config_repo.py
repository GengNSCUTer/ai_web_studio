from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models.tool_config import (
    McpServer,
    McpTool,
    UserToolCredential,
    WorkspaceAgentPolicy,
    WorkspaceToolSetting,
)


class ToolConfigRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_credentials(self, user_id: str) -> list[UserToolCredential]:
        stmt = (
            select(UserToolCredential)
            .where(UserToolCredential.user_id == user_id)
            .order_by(UserToolCredential.provider_key.asc())
        )
        return list(self.db.scalars(stmt).all())

    def get_credential(self, user_id: str, provider_key: str) -> UserToolCredential | None:
        stmt = (
            select(UserToolCredential)
            .where(UserToolCredential.user_id == user_id, UserToolCredential.provider_key == provider_key)
            .limit(1)
        )
        return self.db.scalars(stmt).first()

    def save_credential(self, credential: UserToolCredential) -> UserToolCredential:
        self.db.add(credential)
        self.db.commit()
        self.db.refresh(credential)
        return credential

    def list_workspace_settings(self, project_id: str) -> list[WorkspaceToolSetting]:
        stmt = (
            select(WorkspaceToolSetting)
            .where(WorkspaceToolSetting.project_id == project_id)
            .order_by(WorkspaceToolSetting.tool_key.asc())
        )
        return list(self.db.scalars(stmt).all())

    def get_workspace_setting(self, project_id: str, tool_key: str) -> WorkspaceToolSetting | None:
        stmt = (
            select(WorkspaceToolSetting)
            .where(WorkspaceToolSetting.project_id == project_id, WorkspaceToolSetting.tool_key == tool_key)
            .limit(1)
        )
        return self.db.scalars(stmt).first()

    def save_workspace_setting(self, setting: WorkspaceToolSetting) -> WorkspaceToolSetting:
        self.db.add(setting)
        self.db.commit()
        self.db.refresh(setting)
        return setting

    def get_workspace_policy(self, project_id: str) -> WorkspaceAgentPolicy | None:
        return self.db.get(WorkspaceAgentPolicy, project_id)

    def save_workspace_policy(self, policy: WorkspaceAgentPolicy) -> WorkspaceAgentPolicy:
        self.db.add(policy)
        self.db.commit()
        self.db.refresh(policy)
        return policy

    def list_mcp_servers(self, user_id: str) -> list[McpServer]:
        stmt = (
            select(McpServer)
            .where(McpServer.user_id == user_id)
            .order_by(McpServer.created_at.asc(), McpServer.name.asc())
        )
        return list(self.db.scalars(stmt).all())

    def get_mcp_server(self, *, user_id: str, server_id: str) -> McpServer | None:
        stmt = select(McpServer).where(McpServer.user_id == user_id, McpServer.id == server_id).limit(1)
        return self.db.scalars(stmt).first()

    def get_mcp_server_by_key(self, *, user_id: str, server_key: str) -> McpServer | None:
        stmt = select(McpServer).where(McpServer.user_id == user_id, McpServer.server_key == server_key).limit(1)
        return self.db.scalars(stmt).first()

    def save_mcp_server(self, server: McpServer) -> McpServer:
        self.db.add(server)
        self.db.commit()
        self.db.refresh(server)
        return server

    def delete_mcp_server(self, server: McpServer) -> None:
        self.db.execute(delete(McpTool).where(McpTool.server_id == server.id))
        self.db.delete(server)
        self.db.commit()

    def list_mcp_tools(self, *, user_id: str, enabled_only: bool = False) -> list[tuple[McpTool, McpServer]]:
        stmt = (
            select(McpTool, McpServer)
            .join(McpServer, McpServer.id == McpTool.server_id)
            .where(McpServer.user_id == user_id)
            .order_by(McpServer.name.asc(), McpTool.display_name.asc())
        )
        if enabled_only:
            stmt = stmt.where(
                McpServer.is_enabled.is_(True),
                McpTool.is_enabled.is_(True),
                McpTool.risk_reviewed.is_(True),
            )
        return [(tool, server) for tool, server in self.db.execute(stmt).all()]

    def list_mcp_tools_for_server(self, *, user_id: str, server_id: str) -> list[McpTool]:
        stmt = (
            select(McpTool)
            .join(McpServer, McpServer.id == McpTool.server_id)
            .where(McpServer.user_id == user_id, McpTool.server_id == server_id)
            .order_by(McpTool.display_name.asc())
        )
        return list(self.db.scalars(stmt).all())

    def get_mcp_tool(self, *, user_id: str, tool_id: str) -> tuple[McpTool, McpServer] | None:
        stmt = (
            select(McpTool, McpServer)
            .join(McpServer, McpServer.id == McpTool.server_id)
            .where(McpServer.user_id == user_id, McpTool.id == tool_id)
            .limit(1)
        )
        result = self.db.execute(stmt).first()
        if not result:
            return None
        tool, server = result
        return tool, server

    def get_mcp_tool_by_key(self, *, user_id: str, tool_key: str) -> tuple[McpTool, McpServer] | None:
        stmt = (
            select(McpTool, McpServer)
            .join(McpServer, McpServer.id == McpTool.server_id)
            .where(McpServer.user_id == user_id, McpTool.tool_key == tool_key)
            .limit(1)
        )
        result = self.db.execute(stmt).first()
        if not result:
            return None
        tool, server = result
        return tool, server

    def save_mcp_tool(self, tool: McpTool) -> McpTool:
        self.db.add(tool)
        self.db.commit()
        self.db.refresh(tool)
        return tool

    def flush_mcp_tool(self, tool: McpTool) -> McpTool:
        self.db.add(tool)
        self.db.flush()
        return tool
