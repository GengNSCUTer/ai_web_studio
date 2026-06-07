from __future__ import annotations

from pydantic import BaseModel, Field


class ToolDefinitionResponse(BaseModel):
    tool_key: str
    provider: str
    category: str
    display_name: str
    description: str
    source_type: str = "local_manifest"
    adapter_type: str = "python"
    risk_level: str = "low"
    input_schema: dict = {}
    read_only: bool
    enabled_by_default: bool
    credential_required: bool = True
    credential_provider: str | None = None


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
    mcp_servers: list["McpServerResponse"] = []
    mcp_tools: list["McpToolResponse"] = []


class ToolConnectionTestResponse(BaseModel):
    ok: bool
    provider_key: str
    message: str
    raw: dict | None = None


class McpServerCreate(BaseModel):
    server_key: str = Field(min_length=2, max_length=96)
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None
    url: str = Field(min_length=8)
    transport_type: str = "streamable_http"
    auth_type: str = "none"
    credential_provider: str | None = Field(default=None, max_length=96)
    is_enabled: bool = True


class McpServerUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    description: str | None = None
    url: str | None = None
    transport_type: str | None = None
    auth_type: str | None = None
    credential_provider: str | None = Field(default=None, max_length=96)
    is_enabled: bool | None = None


class McpServerResponse(BaseModel):
    id: str
    server_key: str
    name: str
    description: str | None = None
    url: str
    transport_type: str
    auth_type: str
    credential_provider: str | None = None
    trust_level: str
    is_enabled: bool
    last_sync_at: str | None = None
    last_error: str | None = None


class McpToolUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=180)
    description_override: str | None = None
    category: str | None = Field(default=None, max_length=64)
    is_enabled: bool | None = None
    risk_level: str | None = None
    read_only: bool | None = None
    fixed_arguments: dict | None = None


class McpToolTestRequest(BaseModel):
    arguments: dict = {}


class McpToolResponse(BaseModel):
    id: str
    server_id: str
    server_key: str | None = None
    raw_name: str
    tool_key: str
    display_name: str
    description: str | None = None
    description_override: str | None = None
    input_schema: dict = {}
    fixed_arguments: dict = {}
    category: str
    risk_level: str
    read_only: bool
    is_enabled: bool
    last_seen_at: str | None = None


class McpSyncResponse(BaseModel):
    ok: bool
    server: McpServerResponse
    tools: list[McpToolResponse]
    message: str
