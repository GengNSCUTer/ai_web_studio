from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class UserMemoryCreate(BaseModel):
    memory_type: str = Field(default="fact", max_length=32)
    title: str = Field(max_length=120)
    content: str = Field(min_length=1)
    is_enabled: bool = True
    source_conversation_id: str | None = Field(default=None, max_length=36)
    source_message_ids: str | None = None
    confidence: str | None = Field(default=None, max_length=16)
    expires_at: datetime | None = None
    project_id: str | None = Field(default=None, max_length=36)
    supersedes_memory_id: str | None = Field(default=None, max_length=36)
    expected_target_version: int | None = Field(default=None, ge=1)
    acknowledge_conflicts: bool = False


class UserMemoryUpdate(BaseModel):
    memory_type: str | None = Field(default=None, max_length=32)
    title: str | None = Field(default=None, max_length=120)
    content: str | None = None
    is_enabled: bool | None = None
    source_conversation_id: str | None = Field(default=None, max_length=36)
    source_message_ids: str | None = None
    confidence: str | None = Field(default=None, max_length=16)
    expires_at: datetime | None = None
    expected_version: int | None = Field(default=None, ge=1)


class UserMemoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    memory_type: str
    title: str
    content: str
    source: str
    source_conversation_id: str | None = None
    source_conversation_title: str | None = None
    source_message_ids: str | None = None
    confidence: str | None = None
    is_enabled: bool
    evidence_quote: str | None = None
    status: str = "active"
    project_id: str | None = None
    importance: float = 0.5
    sensitivity: str = "normal"
    risk_level: str = "safe"
    candidate_reason: str | None = None
    conflict_review: dict | None = None
    supersedes_memory_id: str | None = None
    fact_key: str | None = None
    fact_value: str | None = None
    version: int = 1
    expires_at: datetime | None = None
    review_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None


class MemorySuggestRequest(BaseModel):
    conversation_id: str = Field(max_length=36)
    max_candidates: int = Field(default=5, ge=1, le=8)


class MemorySuggestion(BaseModel):
    memory_type: str = Field(max_length=32)
    title: str = Field(max_length=120)
    content: str
    reason: str | None = None
    duplicate_memory_id: str | None = None
    conflict_memory_id: str | None = None
    risk_level: str = "safe"
    risk_reason: str | None = None
    source_conversation_id: str | None = None
    source_message_ids: str | None = None
    confidence: str | None = None
    project_id: str | None = None
    source_message_id: str | None = None
    evidence_quote: str | None = None
    evidence_verified: bool = False
    conflict_review: dict | None = None


class MemorySuggestResponse(BaseModel):
    suggestions: list[MemorySuggestion]


class MemoryReviewRequest(BaseModel):
    expires_at: datetime | None = None
    supersedes_memory_id: str | None = Field(default=None, max_length=36)
    expected_version: int | None = Field(default=None, ge=1)
    expected_target_version: int | None = Field(default=None, ge=1)
    acknowledge_conflicts: bool = False


class MemoryConflictCheckRequest(BaseModel):
    expected_version: int = Field(ge=1)


class MemoryExtractionJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    project_id: str | None = None
    status: str
    attempts: int
    max_attempts: int
    result_count: int
    cursor_end: int = 0
    trigger_action: str = "extract_now"
    trigger_reason: str = "legacy_batch"
    priority: int = 0
    semantic_action: str | None = None
    semantic_confidence: str | None = None
    semantic_model_called: bool = False
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    finished_at: datetime | None = None


class MemoryTriggerLabel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_action: Literal["extract", "extract_now", "revise", "revoke", "buffer", "skip"]
    review_reason: Literal["correct", "missed_trigger", "false_trigger", "needs_context", "wrong_action"]


class MemoryTriggerDecisionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    conversation_id: str
    project_id: str | None = None
    message_id: str
    observed_turn_id: str | None = None
    policy_version: str
    action: str
    reason: str
    outcome: str
    job_id: str | None = None
    job_created: bool
    semantic_action: str | None = None
    semantic_confidence: str | None = None
    semantic_model_called: bool
    expected_action: str | None = None
    review_reason: str | None = None
    reviewed_at: datetime | None = None
    created_at: datetime


class MemoryActivityItem(BaseModel):
    event_id: str
    memory_id: str | None
    job_id: str
    title: str
    content: str
    status: str
    version: int
    created_at: datetime


class MemoryActivityResponse(BaseModel):
    conversation_id: str
    has_pending_jobs: bool
    items: list[MemoryActivityItem]
