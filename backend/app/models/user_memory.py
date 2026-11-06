from datetime import datetime
from uuid import uuid4

from pgvector.sqlalchemy import Vector
from sqlalchemy import Boolean, Computed, DateTime, Float, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.core.memory_search_schema import ANN_DIMENSIONS


class MemorySearchComputed(Computed):
    """SQLite 不承担生产词法检索，只用于原业务单测。"""


@compiles(MemorySearchComputed, "sqlite")
def _sqlite_memory_computed(element, compiler, **kwargs):
    return ""


class UserMemory(Base):
    __tablename__ = "user_memories"
    __table_args__ = (
        Index("ix_user_memories_active_lookup", "user_id", "status", "is_enabled", "id"),
        Index("ix_memory_expiry_lookup", "user_id", "status", "is_enabled", "expires_at"),
        Index("ix_memory_terms", "search_vector", postgresql_using="gin").ddl_if(dialect="postgresql"),
        Index("ix_memory_index_backlog", "updated_at", "id", postgresql_where=text(
            "search_embedding IS NULL AND status = 'active' AND is_enabled = true "
            "AND memory_type NOT IN ('profile', 'instruction')")).ddl_if(dialect="postgresql"),
        *(Index(f"ix_memory_hnsw_{dim}",
                text(f"(search_embedding::{'vector' if dim <= 2000 else 'halfvec'}({dim})) "
                     f"{'vector' if dim <= 2000 else 'halfvec'}_cosine_ops"),
                postgresql_using="hnsw", postgresql_where=text(f"search_dimensions = {dim}"))
          .ddl_if(dialect="postgresql") for dim in ANN_DIMENSIONS),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    memory_type: Mapped[str] = mapped_column(String(32), default="fact")
    title: Mapped[str] = mapped_column(String(120))
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32), default="manual")
    source_conversation_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    source_message_ids: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_quote: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 后台结果与提取任务精确绑定；历史记忆不按时间猜测补关联。
    extraction_job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    confidence: Mapped[str | None] = mapped_column(String(16), nullable=True)
    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # pending 不进入 Prompt；active 来自人工确认或显式开启的保守自动规则。
    status: Mapped[str] = mapped_column(String(24), default="active", index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    importance: Mapped[float] = mapped_column(Float, default=0.5)
    sensitivity: Mapped[str] = mapped_column(String(24), default="normal")
    risk_level: Mapped[str] = mapped_column(String(32), default="safe")
    candidate_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 仅保存服务端生成的关系判断与版本快照，模型不能据此直接激活。
    conflict_review_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # 标识由服务端生成；旧记录按需识别，不在升级时猜测或覆盖用户内容。
    fact_key: Mapped[str | None] = mapped_column(String(180), nullable=True)
    fact_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # 记忆专用向量缓存；签名不同或正文/版本变化时绝不复用，不混入知识库 Chunk。
    embedding_vector: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding_signature: Mapped[str | None] = mapped_column(String(64), nullable=True)
    embedding_text_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 派生字段由 PostgreSQL 在写入时维护；普通 ORM 查询默认不搬运索引数据。
    search_terms: Mapped[str | None] = mapped_column(Text, nullable=True, deferred=True)
    search_vector: Mapped[object | None] = mapped_column(TSVECTOR().with_variant(Text(), "sqlite"),
        MemorySearchComputed("to_tsvector('simple'::regconfig, coalesce(search_terms, ''))", persisted=True),
        nullable=True, deferred=True)
    search_embedding: Mapped[list[float] | None] = mapped_column(Vector(), nullable=True, deferred=True)
    search_dimensions: Mapped[int | None] = mapped_column(Integer, nullable=True, deferred=True)
    embedding_retry_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    supersedes_memory_id: Mapped[str | None] = mapped_column(
        ForeignKey("user_memories.id"), nullable=True, index=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    review_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user = relationship("User", back_populates="memories")


class MemoryExtractionJob(Base):
    """持久化增量提取任务；快照用于拒绝来源被编辑后的旧结果。"""

    __tablename__ = "memory_extraction_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), nullable=False, index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    idempotency_key: Mapped[str] = mapped_column(String(192), unique=True, index=True)
    source_message_ids: Mapped[str] = mapped_column(Text)
    source_snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    cursor_end: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    lease_version: Mapped[int] = mapped_column(Integer, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
