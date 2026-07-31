from __future__ import annotations

from typing import Literal

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


class WorkspaceAgentPolicyResponse(BaseModel):
    project_id: str
    permission_mode: Literal["read_only", "ask", "full_workspace"] = "ask"


class WorkspaceAgentPolicyUpdate(BaseModel):
    permission_mode: Literal["read_only", "ask", "full_workspace"]


class ToolSettingsResponse(BaseModel):
    tools: list[ToolDefinitionResponse]
    credentials: list[UserToolCredentialResponse]
    workspace_settings: list[WorkspaceToolSettingResponse] = []
    workspace_policy: WorkspaceAgentPolicyResponse | None = None
    mcp_servers: list["McpServerResponse"] = []
    mcp_tools: list["McpToolResponse"] = []
    skills: list["SkillInstallationResponse"] = Field(default_factory=list)


class SkillInstallationResponse(BaseModel):
    skill_key: str
    version: str
    display_name: str
    description: str
    instructions: list[str]
    output_contract: list[str]
    required_tool_keys: list[str]
    optional_tool_keys: list[str] = Field(default_factory=list)
    requires_project: bool = False
    requires_tool_execution: bool = True
    activation_examples: list[str] = Field(default_factory=list)
    risk_declaration: str
    is_installed: bool
    is_enabled: bool
    installed_version: str | None = None
    missing_tool_keys: list[str] = Field(default_factory=list)
    available_optional_tool_keys: list[str] = Field(default_factory=list)
    is_ready: bool = False
    unavailable_reason: str | None = None
    installed_manifest_digest: str | None = None
    manifest_digest: str | None = None
    source_kind: str = "builtin"
    source_publisher: str = "AI Web Studio"
    signature_status: str = "repository_attested"
    security_review_status: str = "approved"
    compatibility: dict[str, str] = Field(default_factory=dict)
    durable_eligible: bool = False
    update_available: bool = False


class SkillInstallationUpdate(BaseModel):
    is_enabled: bool = True


class SkillRecommendationResponse(BaseModel):
    skill_key: str
    display_name: str
    description: str
    score: float
    reasons: list[str] = Field(default_factory=list)
    requires_confirmation: bool = True
    is_ready: bool = False


class SkillGoldSetAssessmentRequest(BaseModel):
    case_id: str = Field(min_length=1, max_length=96)
    selected_skill_key: str | None = Field(default=None, max_length=128)
    plan: dict = Field(default_factory=dict)


class SkillGoldSetBatchAssessmentRequest(BaseModel):
    observations: list[SkillGoldSetAssessmentRequest] = Field(min_length=1, max_length=200)


class ToolConnectionTestResponse(BaseModel):
    ok: bool
    provider_key: str
    message: str
    raw: dict | None = None


class McpServerCreate(BaseModel):
    server_key: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None
    url: str = Field(min_length=8)
    transport_type: Literal["streamable_http"] = "streamable_http"
    auth_type: Literal["none", "api_key", "bearer", "api_key_header"] = "none"
    credential_provider: str | None = Field(default=None, max_length=96)
    is_enabled: bool = True


class McpServerUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=128)
    description: str | None = None
    url: str | None = None
    transport_type: Literal["streamable_http"] | None = None
    auth_type: Literal["none", "api_key", "bearer", "api_key_header"] | None = None
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
    risk_level: Literal["low", "medium", "high"] | None = None
    read_only: bool | None = None
    risk_reviewed: bool | None = None
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
    output_schema: dict = {}
    fixed_arguments: dict = {}
    category: str
    risk_level: str
    read_only: bool
    remote_read_only_hint: bool | None = None
    risk_reviewed: bool
    is_enabled: bool
    last_seen_at: str | None = None


class McpSyncResponse(BaseModel):
    ok: bool
    server: McpServerResponse
    tools: list[McpToolResponse]
    message: str
