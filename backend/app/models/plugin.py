"""插件安装与项目启用分离；外部提交不能自行变成审核通过。"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class PluginInstallation(Base):
    __tablename__ = "plugin_installations"
    __table_args__ = (UniqueConstraint("user_id", "plugin_key", name="uq_plugin_user_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    plugin_key: Mapped[str] = mapped_column(String(128))
    release_id: Mapped[str] = mapped_column(String(36))
    installed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PluginRelease(Base):
    """保存安装时服务器审核过的版本，回滚仍重新核验当前能力。"""

    __tablename__ = "plugin_releases"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    plugin_key: Mapped[str] = mapped_column(String(128))
    version: Mapped[str] = mapped_column(String(32))
    manifest_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProjectPlugin(Base):
    __tablename__ = "project_plugins"
    __table_args__ = (UniqueConstraint("project_id", "plugin_key", name="uq_project_plugin"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    plugin_key: Mapped[str] = mapped_column(String(128))
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=False)


class PluginSubmission(Base):
    __tablename__ = "plugin_submissions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16))
    source_url: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="pending_review")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SandboxJob(Base):
    __tablename__ = "sandbox_jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    plugin_key: Mapped[str] = mapped_column(String(128))
    release_id: Mapped[str] = mapped_column(String(36))
    script_key: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="awaiting_approval", index=True)
    plan_json: Mapped[str] = mapped_column(Text)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(String(256), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_version: Mapped[int] = mapped_column(Integer, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SandboxWorkerState(Base):
    __tablename__ = "sandbox_worker_states"
    owner: Mapped[str] = mapped_column(String(128), primary_key=True)
    status: Mapped[str] = mapped_column(String(24))
    detail: Mapped[str] = mapped_column(String(256))
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
