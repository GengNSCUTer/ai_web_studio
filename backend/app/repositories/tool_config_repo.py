from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.tool_config import UserToolCredential, WorkspaceToolSetting


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
