from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255), default="New Chat")
    model_name: Mapped[str] = mapped_column(String(128))
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_summary_boundary_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    context_summary_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_prompt_prefix_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_prompt_prefix_token_count: Mapped[int | None] = mapped_column(nullable=True)
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user = relationship("User", back_populates="conversations")
    project = relationship("Project", back_populates="conversations")
    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")
