from __future__ import annotations

from pydantic import BaseModel, Field


class ToolDefinitionResponse(BaseModel):
    tool_key: str
    provider: str
    category: str
    display_name: str
    description: str
    read_only: bool
    enabled_by_default: bool
    credential_required: bool = True


class UserToolCredentialResponse(BaseModel):
    provider_key: str
    credential_name: str
    is_enabled: bool
    has_api_key: bool
    api_key_masked: str | None = None
    source: str = "user"


class UserToolCredentialUpdate(BaseModel):
    credential_name: str | None = Field(default=None, max_length=128)
    api_key: str | None = None
    clear_api_key: bool | None = None
    is_enabled: bool | None = None


class WorkspaceToolSettingResponse(BaseModel):
    project_id: str
    tool_key: str
    is_enabled: bool


class WorkspaceToolSettingUpdate(BaseModel):
    is_enabled: bool


class ToolSettingsResponse(BaseModel):
    tools: list[ToolDefinitionResponse]
    credentials: list[UserToolCredentialResponse]
    workspace_settings: list[WorkspaceToolSettingResponse] = []


class ToolConnectionTestResponse(BaseModel):
    ok: bool
    provider_key: str
    message: str
