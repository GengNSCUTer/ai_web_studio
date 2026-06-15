from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.upload import UploadItemReference


class MessageCreate(BaseModel):
    role: str = Field(max_length=32)
    content: str


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    role: str
    content: str
    reasoning_content: str | None = None
    external_sources: str | None = None
    tool_events: list[dict[str, Any]] = Field(default_factory=list)
    status: str
    created_at: datetime
    updated_at: datetime | None = None
    attachments: list[UploadItemReference] = Field(default_factory=list)


class MessageBulkDeleteRequest(BaseModel):
    message_ids: list[str] = Field(default_factory=list)


class ChatStreamRequest(BaseModel):
    conversation_id: str | None = None
    title: str | None = Field(default=None, max_length=255)
    content: str
    model_name: str | None = Field(default=None, max_length=128)
    system_prompt: str | None = None
    attachments: list[UploadItemReference] = Field(default_factory=list)
    thinking_enabled: bool = False
    thinking_budget: int | None = None
    web_search_enabled: bool = False
    knowledge_base_id: str | None = Field(default=None, max_length=36)
    knowledge_base_ids: list[str] = Field(default_factory=list, max_length=10)


class ChatRegenerateRequest(BaseModel):
    conversation_id: str
    assistant_message_id: str
    model_name: str | None = Field(default=None, max_length=128)
    system_prompt: str | None = None
    thinking_enabled: bool = False
    thinking_budget: int | None = None
    web_search_enabled: bool = False
    knowledge_base_id: str | None = Field(default=None, max_length=36)
    knowledge_base_ids: list[str] = Field(default_factory=list, max_length=10)


class ChatEditLastUserRequest(BaseModel):
    conversation_id: str
    user_message_id: str
    assistant_message_id: str
    content: str
    attachments: list[UploadItemReference] | None = None
    model_name: str | None = Field(default=None, max_length=128)
    system_prompt: str | None = None
    thinking_enabled: bool = False
    thinking_budget: int | None = None
    web_search_enabled: bool = False
    knowledge_base_id: str | None = Field(default=None, max_length=36)
    knowledge_base_ids: list[str] = Field(default_factory=list, max_length=10)
