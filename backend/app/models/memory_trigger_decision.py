"""长期记忆触发决策审计；只保存摘要、哈希和范围计数，不保存用户原文。"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class MemoryTriggerDecision(Base):
    __tablename__ = "memory_trigger_decisions"
    __table_args__ = (
        UniqueConstraint("conversation_id", "message_id", "observed_turn_id", "source_digest", "policy_version",
                         name="uq_memory_trigger_decision_source"),
        Index("ix_memory_trigger_decisions_conversation_created", "conversation_id", "created_at"),
        Index("ix_memory_trigger_decisions_job", "job_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True)
    message_id: Mapped[str] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), nullable=False)
    observed_turn_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id", ondelete="CASCADE"), nullable=True)
    source_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False, default="rules-v2")
    action: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str] = mapped_column(String(64), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("memory_extraction_jobs.id", ondelete="SET NULL"), nullable=True)
    job_created: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False, default="buffered")
    context_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    semantic_action: Mapped[str | None] = mapped_column(String(24), nullable=True)
    semantic_confidence: Mapped[str | None] = mapped_column(String(16), nullable=True)
    semantic_model_called: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    expected_action: Mapped[str | None] = mapped_column(String(24), nullable=True)
    review_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
