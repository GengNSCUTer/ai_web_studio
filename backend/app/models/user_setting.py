from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.config import settings as app_settings


class UserSetting(Base):
    __tablename__ = "user_settings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True, unique=True)
    provider_type: Mapped[str] = mapped_column(String(32), default="ollama")
    default_model: Mapped[str] = mapped_column(String(128), default=app_settings.ollama_default_model)
    ollama_base_url: Mapped[str] = mapped_column(String(255), default=app_settings.ollama_base_url)
    api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    temperature: Mapped[float] = mapped_column(Float, default=0.7)
    top_p: Mapped[float] = mapped_column(Float, default=0.9)
    max_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_context_window: Mapped[int] = mapped_column(Integer, default=128000)
    context_mode: Mapped[str] = mapped_column(String(32), default="balanced")
    ui_language: Mapped[str] = mapped_column(String(16), default="zh-CN")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user = relationship("User", back_populates="settings")
