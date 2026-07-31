from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.upload import UploadItemReference


MessageId = Annotated[str, Field(min_length=1, max_length=64)]
MessageContent = Annotated[str, Field(min_length=1, max_length=200000)]


class MessageCreate(BaseModel):
    # 公开消息创建接口只允许创建用户消息；assistant/system 消息必须由聊天编排内部写入。
    role: Literal["user"] = "user"
    content: MessageContent

    @field_validator("content")
    @classmethod
    def strip_non_empty_content(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("消息内容不能为空")
        return normalized


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
    message_ids: list[MessageId] = Field(default_factory=list, max_length=200)


class ChatStreamRequest(BaseModel):
    conversation_id: str | None = None
    title: str | None = Field(default=None, max_length=255)
    content: MessageContent
    model_name: str | None = Field(default=None, max_length=128)
    system_prompt: str | None = None
    attachments: list[UploadItemReference] = Field(default_factory=list)
    thinking_enabled: bool = False
    thinking_budget: int | None = None
    web_search_enabled: bool = False
    knowledge_base_id: str | None = Field(default=None, max_length=36)
    knowledge_base_ids: list[str] = Field(default_factory=list, max_length=10)
    skill_key: str | None = Field(default=None, max_length=128)

    @field_validator("content")
    @classmethod
    def strip_non_empty_content(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("消息内容不能为空")
        return normalized


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
    skill_key: str | None = Field(default=None, max_length=128)


class ChatEditLastUserRequest(BaseModel):
    conversation_id: str
    user_message_id: str
    assistant_message_id: str
    content: MessageContent
    attachments: list[UploadItemReference] | None = None
    model_name: str | None = Field(default=None, max_length=128)
    system_prompt: str | None = None
    thinking_enabled: bool = False
    thinking_budget: int | None = None
    web_search_enabled: bool = False
    knowledge_base_id: str | None = Field(default=None, max_length=36)
    knowledge_base_ids: list[str] = Field(default_factory=list, max_length=10)
    skill_key: str | None = Field(default=None, max_length=128)

    @field_validator("content")
    @classmethod
    def strip_non_empty_content(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("消息内容不能为空")
        return normalized
