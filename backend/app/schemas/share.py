from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.message import MessageResponse


class ConversationShareCreate(BaseModel):
    expires_in_days: int | None = Field(default=None, ge=1, le=365)


class ConversationShareUpdate(BaseModel):
    is_enabled: bool | None = None
    expires_in_days: int | None = Field(default=None, ge=1, le=365)
    clear_expires_at: bool = False


class ConversationShareResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    token: str
    conversation_id: str
    is_enabled: bool
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None


class PublicConversationShareResponse(BaseModel):
    token: str
    title: str
    model_name: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    messages: list[MessageResponse]
