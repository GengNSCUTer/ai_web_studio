from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class UserSettingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str | None = None
    provider_type: str
    default_model: str
    ollama_base_url: str
    api_key: str | None = None
    temperature: float
    top_p: float
    max_tokens: int | None = None
    system_prompt: str | None = None
    model_context_window: int
    context_mode: str
    memory_enabled: bool
    memory_max_chars: int
    ui_language: str
    theme_mode: str
    updated_at: datetime | None = None


class UserSettingUpdate(BaseModel):
    provider_type: str | None = Field(default=None, max_length=32)
    default_model: str | None = Field(default=None, max_length=128)
    ollama_base_url: str | None = Field(default=None, max_length=255)
    api_key: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    system_prompt: str | None = None
    model_context_window: int | None = None
    context_mode: str | None = Field(default=None, max_length=32)
    memory_enabled: bool | None = None
    memory_max_chars: int | None = None
    ui_language: str | None = Field(default=None, max_length=16)
    theme_mode: str | None = Field(default=None, max_length=16)


class ProviderConnectionTestRequest(BaseModel):
    provider_type: str = Field(max_length=32)
    ollama_base_url: str = Field(max_length=255)
    api_key: str | None = None


class ProviderConnectionTestResponse(BaseModel):
    ok: bool
    provider: str
    base_url: str
    models: list[str]
    default_model: str | None = None
    message: str
