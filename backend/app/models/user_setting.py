from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.config import settings as app_settings


class UserSetting(Base):
    # 每个用户最多一条设置记录；具体默认值和归一化规则主要由 SettingService 管。
    __tablename__ = "user_settings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    # unique=True 保证一个用户只有一份设置，是 get_or_create_user_settings 的数据库兜底。
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, unique=True)
    provider_type: Mapped[str] = mapped_column(String(32), default="ollama")
    default_model: Mapped[str] = mapped_column(String(128), default=app_settings.ollama_default_model)
    ollama_base_url: Mapped[str] = mapped_column(String(255), default=app_settings.ollama_base_url)
    api_base_url: Mapped[str] = mapped_column(String(255), default="https://api.siliconflow.cn/v1")
    # 以下 key 字段保存的是 SecretService 加密后的密文或历史明文兼容值，不应直接返回给前端。
    api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    temperature: Mapped[float] = mapped_column(Float, default=0.7)
    top_p: Mapped[float] = mapped_column(Float, default=0.9)
    max_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_context_window: Mapped[int] = mapped_column(Integer, default=128000)
    context_mode: Mapped[str] = mapped_column(String(32), default="balanced")
    memory_enabled: Mapped[bool] = mapped_column(default=True)
    memory_max_chars: Mapped[int] = mapped_column(Integer, default=4000)
    memory_auto_candidate_enabled: Mapped[bool] = mapped_column(default=False)
    memory_auto_candidate_turn_interval: Mapped[int] = mapped_column(Integer, default=4)
    ui_language: Mapped[str] = mapped_column(String(16), default="zh-CN")
    theme_mode: Mapped[str] = mapped_column(String(16), default="system")
    knowledge_parser_provider: Mapped[str] = mapped_column(String(32), default="local_basic")
    knowledge_embedding_provider: Mapped[str] = mapped_column(String(32), default="siliconflow")
    knowledge_embedding_base_url: Mapped[str] = mapped_column(String(255), default="https://api.siliconflow.cn/v1")
    knowledge_embedding_model: Mapped[str] = mapped_column(String(128), default="BAAI/bge-m3")
    knowledge_embedding_dimensions: Mapped[int] = mapped_column(Integer, default=1024)
    knowledge_rerank_enabled: Mapped[bool] = mapped_column(default=True)
    knowledge_rerank_provider: Mapped[str] = mapped_column(String(32), default="siliconflow")
    knowledge_rerank_base_url: Mapped[str] = mapped_column(String(255), default="https://api.siliconflow.cn/v1")
    knowledge_rerank_model: Mapped[str] = mapped_column(String(128), default="BAAI/bge-reranker-v2-m3")
    knowledge_embedding_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    knowledge_rerank_api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user = relationship("User", back_populates="settings")
