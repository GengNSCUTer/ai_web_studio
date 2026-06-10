from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.core.config import settings
from app.repositories.tool_config_repo import ToolConfigRepository
from app.services.secret_service import SecretService


@dataclass
class ToolCredential:
    provider_key: str
    api_key: str | None
    source: str
    is_enabled: bool


class ToolCredentialResolver:
    def __init__(self, db: Session | None = None) -> None:
        self.db = db
        self.repo = ToolConfigRepository(db) if db else None
        self.secrets = SecretService()

    def resolve(self, *, user_id: str | None, provider_key: str) -> ToolCredential:
        if self.repo and user_id:
            credential = self.repo.get_credential(user_id, provider_key)
            if credential:
                if not credential.is_enabled:
                    return ToolCredential(
                        provider_key=provider_key,
                        api_key=None,
                        source="user_disabled",
                        is_enabled=False,
                    )
                api_key = self.secrets.decrypt(credential.api_key)
                if not api_key:
                    env_key = self._env_api_key(provider_key)
                    return ToolCredential(
                        provider_key=provider_key,
                        api_key=env_key,
                        source="env" if env_key else "missing",
                        is_enabled=bool(env_key),
                    )
                return ToolCredential(
                    provider_key=provider_key,
                    api_key=api_key,
                    source="user",
                    is_enabled=True,
                )

        env_key = self._env_api_key(provider_key)
        return ToolCredential(
            provider_key=provider_key,
            api_key=env_key,
            source="env" if env_key else "missing",
            is_enabled=bool(env_key),
        )

    @staticmethod
    def _env_api_key(provider_key: str) -> str | None:
        if provider_key == "tavily":
            return settings.tavily_api_key.strip() or None
        if provider_key == "amap":
            return settings.amap_api_key.strip() or None
        if provider_key == "mineru":
            return settings.mineru_api_token.strip() or None
        return None

    def is_tool_enabled_for_workspace(self, *, project_id: str | None, tool_key: str) -> bool:
        if not self.repo or not project_id:
            return True
        setting = self.repo.get_workspace_setting(project_id, tool_key)
        if not setting:
            return True
        return bool(setting.is_enabled)
