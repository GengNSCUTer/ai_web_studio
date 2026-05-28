from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, String, Text, UniqueConstraint, func
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
