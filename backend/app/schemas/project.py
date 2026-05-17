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
