from datetime import datetime

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


class UserMemoryUpdate(BaseModel):
    memory_type: str | None = Field(default=None, max_length=32)
    title: str | None = Field(default=None, max_length=120)
    content: str | None = None
    is_enabled: bool | None = None
    source_conversation_id: str | None = Field(default=None, max_length=36)
    source_message_ids: str | None = None
    confidence: str | None = Field(default=None, max_length=16)
    expires_at: datetime | None = None


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
    status: str = "active"
    project_id: str | None = None
    importance: float = 0.5
    sensitivity: str = "normal"
    risk_level: str = "safe"
    candidate_reason: str | None = None
    supersedes_memory_id: str | None = None
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


class MemorySuggestResponse(BaseModel):
    suggestions: list[MemorySuggestion]


class MemoryReviewRequest(BaseModel):
    expires_at: datetime | None = None
    supersedes_memory_id: str | None = Field(default=None, max_length=36)


class MemoryExtractionJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    project_id: str | None = None
    status: str
    attempts: int
    max_attempts: int
    result_count: int
    error_code: str | None = None
    error_message: str | None = None
    created_at: datetime
    finished_at: datetime | None = None
