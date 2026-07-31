from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AgentRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str | None = None
    conversation_id: str | None = None
    assistant_message_id: str | None = None
    runtime_kind: str = "file_edit"
    status: str
    state_version: int
    max_steps: int
    current_step: int
    created_at: datetime
    updated_at: datetime | None = None
    finished_at: datetime | None = None


class AgentStepResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    sequence: int
    call_id: str
    tool_key: str
    arguments_hash: str
    status: str
    attempts: int
    max_attempts: int = 3
    lease_version: int = 0
    available_at: datetime | None = None
    dead_lettered_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None
    finished_at: datetime | None = None


class AgentApprovalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    step_id: str
    action: str
    arguments_hash: str
    status: str
    decision_mode: str
    expires_at: datetime
    consumed_at: datetime | None = None


class PatchDraftResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    step_id: str
    project_file_id: str
    base_revision_id: str
    base_content_hash: str
    proposed_content_hash: str
    diff_text: str
    arguments_hash: str
    status: str
    expires_at: datetime
    applied_at: datetime | None = None


class AgentArtifactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    run_id: str
    step_id: str
    artifact_type: str
    content_hash: str
    preview: str
    char_count: int
    prompt_state: str
    created_at: datetime


class AgentOutboxEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    step_id: str
    event_type: str
    status: str
    attempt_count: int
    lease_version: int
    available_at: datetime | None = None
    dead_lettered_at: datetime | None = None
    error_code: str | None = None
    error_message: str | None = None


class AgentRunSnapshotResponse(BaseModel):
    run: AgentRunResponse
    steps: list[AgentStepResponse]
    checkpoint: dict[str, Any] | None = None
    approvals: list[AgentApprovalResponse]
    drafts: list[PatchDraftResponse]
    artifacts: list[AgentArtifactResponse] = Field(default_factory=list)
    outbox_events: list[AgentOutboxEventResponse] = Field(default_factory=list)


class ApprovalChallengeResponse(BaseModel):
    approval_id: str
    approval_token: str


class ApprovalApplyRequest(BaseModel):
    approval_token: str = Field(min_length=20, max_length=256)


class FileEditApplyResponse(BaseModel):
    run_id: str
    step_id: str
    patch_draft_id: str
    approval_id: str
    file_id: str
    revision_id: str | None = None
    revision_number: int | None = None
    status: str


class FileEditProposalResponse(BaseModel):
    run_id: str
    step_id: str
    patch_draft_id: str
    approval_id: str
    file_id: str
    file_name: str
    diff_text: str
    arguments_hash: str
    expires_at: datetime


class FileRevisionRestoreRequest(BaseModel):
    conversation_id: str | None = Field(default=None, max_length=36)
    assistant_message_id: str | None = Field(default=None, max_length=36)


class DurableToolCallRequest(BaseModel):
    call_id: str | None = Field(default=None, min_length=1, max_length=64)
    tool_key: str = Field(min_length=1, max_length=160)
    arguments: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list, max_length=12)
    result_bindings: list[dict[str, Any]] = Field(default_factory=list, max_length=8)


class DurableToolRunRequest(BaseModel):
    project_id: str | None = Field(default=None, max_length=36)
    conversation_id: str | None = Field(default=None, max_length=36)
    assistant_message_id: str | None = Field(default=None, max_length=36)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    max_attempts: int = Field(default=3, ge=1, le=5)
    calls: list[DurableToolCallRequest] = Field(min_length=1, max_length=12)


class FileRevisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_file_id: str
    revision_number: int
    content_hash: str
    created_by: str
    source_run_id: str | None = None
    source_step_id: str | None = None
    created_at: datetime
