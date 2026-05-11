from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UserMemoryCreate(BaseModel):
    memory_type: str = Field(default="fact", max_length=32)
    title: str = Field(max_length=120)
    content: str = Field(min_length=1)
    is_enabled: bool = True


class UserMemoryUpdate(BaseModel):
    memory_type: str | None = Field(default=None, max_length=32)
    title: str | None = Field(default=None, max_length=120)
    content: str | None = None
    is_enabled: bool | None = None


class UserMemoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    memory_type: str
    title: str
    content: str
    source: str
    is_enabled: bool
    created_at: datetime
    updated_at: datetime | None = None
