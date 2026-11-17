"""会话摘要版本和持续重建进度；不存Provider凭据或额外原文副本。"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ConversationSummaryCheckpoint(Base):
    __tablename__ = "conversation_summary_checkpoints"
    __table_args__ = (Index("ix_summary_checkpoint_order", "conversation_id", "boundary_created_at", "boundary_sequence", "boundary_message_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    summary_text: Mapped[str] = mapped_column(Text)
    boundary_message_id: Mapped[str] = mapped_column(String(36))
    boundary_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    boundary_sequence: Mapped[int] = mapped_column(Integer)
    source_revision: Mapped[int] = mapped_column(Integer)
    mode: Mapped[str] = mapped_column(String(16), default="model")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ConversationSummaryJob(Base):
    __tablename__ = "conversation_summary_jobs"
    __table_args__ = (Index("ix_summary_job_claim", "status", "next_attempt_at", "created_at"),
                     Index("ix_summary_job_conversation", "conversation_id", "source_revision"))

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id", ondelete="CASCADE"))
    source_revision: Mapped[int] = mapped_column(Integer)
    target_message_id: Mapped[str] = mapped_column(String(36))
    cursor_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    partial_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    partial_offset: Mapped[int] = mapped_column(Integer, default=0)
    partial_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(24), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    batches: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_version: Mapped[int] = mapped_column(Integer, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
