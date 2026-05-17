from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None
    default_model: str | None = Field(default=None, max_length=128)
    system_prompt: str | None = None


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    default_model: str | None = Field(default=None, max_length=128)
    system_prompt: str | None = None


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    name: str
    description: str | None = None
    default_model: str | None = None
    system_prompt: str | None = None
    created_at: datetime
    updated_at: datetime | None = None


class ProjectFileCreate(BaseModel):
    id: str | None = Field(default=None, max_length=36)
    file_name: str = Field(min_length=1, max_length=255)
    mime_type: str | None = Field(default=None, max_length=128)
    file_size: int | None = None
    kind: str = Field(default="file", max_length=32)
    storage_key: str = Field(min_length=1)
    parsed_text: str | None = None


class ProjectFileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    file_name: str
    mime_type: str | None = None
    file_size: int | None = None
    kind: str
    storage_key: str
    parsed_text: str | None = None
    created_at: datetime


class ProjectStatsResponse(BaseModel):
    project_id: str
    conversation_count: int
    message_count: int
    file_count: int
    prompt_template_count: int
    total_file_size: int
