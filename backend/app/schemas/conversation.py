from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import settings


class ConversationCreate(BaseModel):
    # 创建会话时，project_id 只是“想放进哪个工作区”；route/service 必须确认该 project 属于当前用户。
    title: str = Field(default="New Chat", max_length=255)
    model_name: str = Field(default=settings.ollama_default_model, max_length=128)
    system_prompt: str | None = None
    project_id: str | None = Field(default=None, max_length=36)

    @field_validator("title", "model_name")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("字段不能为空")
        return normalized

    @field_validator("system_prompt", "project_id")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class ConversationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 只暴露用户可编辑字段。context_summary 属于上下文治理内部状态，不允许普通 PATCH 改写。
    title: str | None = Field(default=None, max_length=255)
    system_prompt: str | None = None
    is_archived: bool | None = None
    is_pinned: bool | None = None
    project_id: str | None = Field(default=None, max_length=36)

    @field_validator("title")
    @classmethod
    def strip_optional_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("会话标题不能为空")
        return normalized

    @field_validator("system_prompt", "project_id")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class ConversationListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str | None = None
    title: str
    model_name: str
    is_pinned: bool
    is_archived: bool
    context_summary: str | None = None
    context_summary_boundary_message_id: str | None = None
    context_summary_updated_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None


class ConversationResponse(ConversationListItem):
    system_prompt: str | None = None
    user_id: str | None = None
