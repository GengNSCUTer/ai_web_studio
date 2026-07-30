from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AgentRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str | None = None
    conversation_id: str | None = None
    assistant_message_id: str | None = None
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


class AgentRunSnapshotResponse(BaseModel):
    run: AgentRunResponse
    steps: list[AgentStepResponse]
    checkpoint: dict[str, Any] | None = None
    approvals: list[AgentApprovalResponse]
    drafts: list[PatchDraftResponse]


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
