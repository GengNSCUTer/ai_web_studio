from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PromptTemplateCreate(BaseModel):
    project_id: str | None = Field(default=None, max_length=36)
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None
    content: str = Field(min_length=1)
    default_model: str | None = Field(default=None, max_length=128)
    category: str | None = Field(default=None, max_length=64)
    variables: str | None = None
    is_default: bool = False


class PromptTemplateUpdate(BaseModel):
    project_id: str | None = Field(default=None, max_length=36)
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    content: str | None = Field(default=None, min_length=1)
    default_model: str | None = Field(default=None, max_length=128)
    category: str | None = Field(default=None, max_length=64)
    variables: str | None = None
    is_default: bool | None = None


class PromptTemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    project_id: str | None = None
    name: str
    description: str | None = None
    content: str
    default_model: str | None = None
    category: str | None = None
    variables: str | None = None
    is_default: bool
    created_at: datetime
    updated_at: datetime | None = None
