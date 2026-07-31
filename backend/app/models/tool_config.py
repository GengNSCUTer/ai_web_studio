from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UserToolCredential(Base):
    __tablename__ = "user_tool_credentials"
    __table_args__ = (UniqueConstraint("user_id", "provider_key", name="uq_user_tool_provider"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    provider_key: Mapped[str] = mapped_column(String(64), index=True)
    credential_name: Mapped[str] = mapped_column(String(128), default="默认凭证")
    api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class WorkspaceToolSetting(Base):
    __tablename__ = "workspace_tool_settings"
    __table_args__ = (UniqueConstraint("project_id", "tool_key", name="uq_workspace_tool_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    tool_key: Mapped[str] = mapped_column(String(128), index=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    config_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class WorkspaceAgentPolicy(Base):
    __tablename__ = "workspace_agent_policies"

    project_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    # read_only: no side effects; ask: durable approval; full_workspace:
    # auto-apply only explicitly allowlisted, workspace-scoped capabilities.
    permission_mode: Mapped[str] = mapped_column(String(32), default="ask")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class McpServer(Base):
    __tablename__ = "mcp_servers"
    __table_args__ = (UniqueConstraint("user_id", "server_key", name="uq_mcp_server_user_key"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    server_key: Mapped[str] = mapped_column(String(96), index=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    transport_type: Mapped[str] = mapped_column(String(32), default="streamable_http")
    url: Mapped[str] = mapped_column(Text)
    auth_type: Mapped[str] = mapped_column(String(32), default="none")
    credential_provider: Mapped[str | None] = mapped_column(String(96), nullable=True, index=True)
    trust_level: Mapped[str] = mapped_column(String(32), default="user_added")
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class McpTool(Base):
    __tablename__ = "mcp_tools"
    __table_args__ = (UniqueConstraint("server_id", "raw_name", name="uq_mcp_tool_server_raw_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    server_id: Mapped[str] = mapped_column(String(36), index=True)
    raw_name: Mapped[str] = mapped_column(String(160), index=True)
    tool_key: Mapped[str] = mapped_column(String(220), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(180))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_override: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_schema_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    output_schema_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    annotations_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    fixed_arguments_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(64), default="mcp_tool")
    risk_level: Mapped[str] = mapped_column(String(32), default="low")
    read_only: Mapped[bool] = mapped_column(Boolean, default=True)
    # 远程 Server 的 annotations 只是提示，不能直接当作本地安全策略；需用户显式审核。
    risk_reviewed: Mapped[bool] = mapped_column(Boolean, default=False)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    call_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class UserSkillInstallation(Base):
    """Per-user enable state for a reviewed Skill manifest.

    Skills are instructions/workflows over existing capabilities, not a way to
    add arbitrary executable code or bypass the workspace policy.
    """

    __tablename__ = "user_skill_installations"
    __table_args__ = (UniqueConstraint("user_id", "skill_key", name="uq_user_skill_installation"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    skill_key: Mapped[str] = mapped_column(String(128), index=True)
    manifest_version: Mapped[str] = mapped_column(String(32))
    # 版本号不足以防止“同版本内容被静默替换”。安装时同时锁定审核过的
    # canonical manifest digest；执行时按这个快照解析，而不是自动漂移到最新文件。
    manifest_digest: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SkillInstallationRevision(Base):
    """Append-only Skill release snapshot for explicit upgrade and rollback.

    The current manifest remains the platform's published catalog. A user may
    continue running a previously reviewed snapshot until they explicitly
    upgrade, provided all referenced tools still pass today's runtime checks.
    This is release history, not arbitrary user-supplied executable content.
    """

    __tablename__ = "skill_installation_revisions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(String(36), index=True)
    skill_key: Mapped[str] = mapped_column(String(128), index=True)
    manifest_version: Mapped[str] = mapped_column(String(32), index=True)
    manifest_digest: Mapped[str] = mapped_column(String(64), index=True)
    manifest_json: Mapped[str] = mapped_column(Text)
    source_kind: Mapped[str] = mapped_column(String(32), default="builtin")
    security_review_status: Mapped[str] = mapped_column(String(32), default="approved")
    action: Mapped[str] = mapped_column(String(32), default="install", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
