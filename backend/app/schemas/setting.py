from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


ChatProviderType = Literal["ollama", "openai-compatible", "vllm"]


class UserSettingResponse(BaseModel):
    # 响应模型永远不回显密钥明文，只返回 has_* 和 masked 字段给前端展示状态。
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str | None = None
    provider_type: str
    default_model: str
    ollama_base_url: str
    api_base_url: str
    api_key: str | None = None
    has_api_key: bool = False
    api_key_masked: str | None = None
    temperature: float
    top_p: float
    max_tokens: int | None = Field(default=None, ge=1, le=131072)
    system_prompt: str | None = None
    model_context_window: int
    context_mode: str
    memory_enabled: bool
    memory_max_chars: int
    ui_language: str
    theme_mode: str
    knowledge_parser_provider: str
    knowledge_embedding_provider: str
    knowledge_embedding_base_url: str
    knowledge_embedding_model: str
    knowledge_embedding_dimensions: int
    knowledge_rerank_enabled: bool
    knowledge_rerank_provider: str
    knowledge_rerank_base_url: str
    knowledge_rerank_model: str
    knowledge_embedding_api_key: str | None = None
    knowledge_embedding_has_api_key: bool = False
    knowledge_embedding_api_key_masked: str | None = None
    knowledge_rerank_api_key: str | None = None
    knowledge_rerank_has_api_key: bool = False
    knowledge_rerank_api_key_masked: str | None = None
    updated_at: datetime | None = None


class UserSettingUpdate(BaseModel):
    # Update Schema 表示“局部更新”；None 和未传字段在 service 中有不同语义。
    provider_type: ChatProviderType | None = None
    default_model: str | None = Field(default=None, max_length=128)
    ollama_base_url: str | None = Field(default=None, max_length=255)
    api_base_url: str | None = Field(default=None, max_length=255)
    api_key: str | None = None
    clear_api_key: bool | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = Field(default=None, ge=1, le=131072)
    system_prompt: str | None = None
    model_context_window: int | None = None
    context_mode: str | None = Field(default=None, max_length=32)
    memory_enabled: bool | None = None
    memory_max_chars: int | None = None
    ui_language: str | None = Field(default=None, max_length=16)
    theme_mode: str | None = Field(default=None, max_length=16)
    knowledge_parser_provider: str | None = Field(default=None, max_length=32)
    knowledge_embedding_provider: str | None = Field(default=None, max_length=32)
    knowledge_embedding_base_url: str | None = Field(default=None, max_length=255)
    knowledge_embedding_model: str | None = Field(default=None, max_length=128)
    knowledge_embedding_dimensions: int | None = Field(default=None, ge=128, le=4096)
    knowledge_rerank_enabled: bool | None = None
    knowledge_rerank_provider: str | None = Field(default=None, max_length=32)
    knowledge_rerank_base_url: str | None = Field(default=None, max_length=255)
    knowledge_rerank_model: str | None = Field(default=None, max_length=128)
    knowledge_embedding_api_key: str | None = None
    clear_knowledge_embedding_api_key: bool | None = None
    knowledge_rerank_api_key: str | None = None
    clear_knowledge_rerank_api_key: bool | None = None


class ProviderConnectionTestRequest(BaseModel):
    # 测试连接是临时动作，不等于保存设置；保存仍走 PATCH /settings。
    provider_type: ChatProviderType
    ollama_base_url: str = Field(max_length=255)
    api_base_url: str | None = Field(default=None, max_length=255)
    api_key: str | None = None


class ProviderConnectionTestResponse(BaseModel):
    ok: bool
    provider: str
    base_url: str
    models: list[str]
    default_model: str | None = None
    message: str


class KnowledgeModelOptionsRequest(BaseModel):
    # 同一个接口服务 embedding/rerank 两类模型候选，model_kind 决定使用哪类用户密钥。
    provider: str = Field(max_length=32)
    base_url: str = Field(max_length=255)
    api_key: str | None = None
    model_kind: str = Field(default="embedding", max_length=32)
    strict: bool = False


class KnowledgeModelOptionsResponse(BaseModel):
    ok: bool
    provider: str
    base_url: str
    model_kind: str
    models: list[str]
    source: str
    message: str
