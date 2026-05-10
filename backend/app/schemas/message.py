from datetime import datetime

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
