from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.config import settings


class ConversationCreate(BaseModel):
    title: str = Field(default="New Chat", max_length=255)
    model_name: str = Field(default=settings.ollama_default_model, max_length=128)
    system_prompt: str | None = None


class ConversationUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=255)
    system_prompt: str | None = None
    is_archived: bool | None = None
    context_summary: str | None = None


class ConversationListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    model_name: str
    is_archived: bool
    context_summary: str | None = None
    context_summary_boundary_message_id: str | None = None
    context_summary_updated_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None


class ConversationResponse(ConversationListItem):
    system_prompt: str | None = None
    user_id: str | None = None
