from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PromptTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None
    content: str = Field(min_length=1)
    default_model: str | None = Field(default=None, max_length=128)
    is_default: bool = False


class PromptTemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    content: str | None = Field(default=None, min_length=1)
    default_model: str | None = Field(default=None, max_length=128)
    is_default: bool | None = None


class PromptTemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    name: str
    description: str | None = None
    content: str
    default_model: str | None = None
    is_default: bool
    created_at: datetime
    updated_at: datetime | None = None
