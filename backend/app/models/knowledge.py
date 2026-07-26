from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector

from app.core.database import Base


class KnowledgeBase(Base):
    __tablename__ = "knowledge_bases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    visibility: Mapped[str] = mapped_column(String(32), default="private")
    parser_provider: Mapped[str] = mapped_column(String(32), default="local_basic")
    chunk_mode: Mapped[str] = mapped_column(String(32), default="general")
    chunk_size: Mapped[int] = mapped_column(Integer, default=1000)
    chunk_overlap: Mapped[int] = mapped_column(Integer, default=150)
    chunk_delimiter: Mapped[str] = mapped_column(String(64), default="\n\n")
    parent_chunk_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    child_chunk_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    child_chunk_overlap: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding_provider: Mapped[str] = mapped_column(String(32), default="siliconflow")
    embedding_model: Mapped[str] = mapped_column(String(128), default="BAAI/bge-m3")
    embedding_dimensions: Mapped[int] = mapped_column(Integer, default=1024)
    rerank_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    rerank_provider: Mapped[str] = mapped_column(String(32), default="siliconflow")
    rerank_model: Mapped[str] = mapped_column(String(128), default="BAAI/bge-reranker-v2-m3")
    retrieval_mode: Mapped[str] = mapped_column(String(32), default="vector")
    retrieval_top_k: Mapped[int] = mapped_column(Integer, default=20)
    rerank_top_n: Mapped[int] = mapped_column(Integer, default=6)
    score_threshold: Mapped[float] = mapped_column(Float, default=0.2)
    max_context_chunks: Mapped[int] = mapped_column(Integer, default=6)
    max_context_chars: Mapped[int] = mapped_column(Integer, default=12000)
    strict_knowledge_answer: Mapped[bool] = mapped_column(Boolean, default=False)
    # "legacy" 兼容现有单文件索引；新发布器将切换为 UUID generation。
    active_index_generation: Mapped[str] = mapped_column(String(64), default="legacy", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    documents = relationship("KnowledgeDocument", back_populates="knowledge_base", cascade="all, delete-orphan")
    jobs = relationship("KnowledgeJob", back_populates="knowledge_base", cascade="all, delete-orphan")
    eval_sets = relationship("KnowledgeEvalSet", back_populates="knowledge_base", cascade="all, delete-orphan")


class KnowledgeIndexGeneration(Base):
    __tablename__ = "knowledge_index_generations"

    id: Mapped[str] = mapped_column(String(64), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    knowledge_base_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id"), index=True)
    base_generation_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="building", index=True)
    manifest_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chunk_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KnowledgeDocument(Base):
    __tablename__ = "knowledge_documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    knowledge_base_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    file_name: Mapped[str] = mapped_column(String(255))
    mime_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    file_size: Mapped[int | None] = mapped_column(Integer, nullable=True)
    storage_key: Mapped[str] = mapped_column(Text)
    parser_provider: Mapped[str] = mapped_column(String(32), default="local_basic")
    parse_status: Mapped[str] = mapped_column(String(32), default="pending")
    index_status: Mapped[str] = mapped_column(String(32), default="pending")
    document_version: Mapped[int] = mapped_column(Integer, default=1)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parsed_markdown_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    parsed_assets_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    knowledge_base = relationship("KnowledgeBase", back_populates="documents")
    jobs = relationship("KnowledgeJob", back_populates="document", cascade="all, delete-orphan")
    chunks = relationship("KnowledgeChunk", back_populates="document", cascade="all, delete-orphan")


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunks"
    __table_args__ = (
        UniqueConstraint(
            "knowledge_base_id",
            "index_generation",
            "vector_id",
            name="uq_knowledge_chunks_generation_vector",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    knowledge_base_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("knowledge_documents.id"), index=True)
    index_generation: Mapped[str] = mapped_column(String(64), default="legacy", index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    vector_id: Mapped[int] = mapped_column(Integer, index=True)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    # 不在列类型上写死维度：不同知识库可以使用不同 Embedding 模型。
    # 先允许 null 以便 legacy Chunk 渐进回填；只有向量已就绪的 generation 才能激活。
    embedding: Mapped[list[float] | None] = mapped_column(Vector(), nullable=True)
    embedding_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    embedding_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    embedding_dimensions: Mapped[int | None] = mapped_column(Integer, nullable=True)
    embedding_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    char_count: Mapped[int] = mapped_column(Integer)
    token_estimate: Mapped[int] = mapped_column(Integer)
    source_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    document = relationship("KnowledgeDocument", back_populates="chunks")


class KnowledgeJob(Base):
    __tablename__ = "knowledge_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    knowledge_base_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id"), index=True)
    document_id: Mapped[str | None] = mapped_column(ForeignKey("knowledge_documents.id"), nullable=True, index=True)
    job_type: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), default="pending")
    idempotency_key: Mapped[str | None] = mapped_column(String(192), nullable=True, unique=True, index=True)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    lease_version: Mapped[int] = mapped_column(Integer, default=0)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # 机器可读的失败分类；error_message 只保存脱敏后的用户可见文案。
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dead_lettered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    knowledge_base = relationship("KnowledgeBase", back_populates="jobs")
    document = relationship("KnowledgeDocument", back_populates="jobs")


class OutboxEvent(Base):
    """PostgreSQL-side delivery intent; publishing to Redis is intentionally at-least-once."""

    __tablename__ = "outbox_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    event_key: Mapped[str] = mapped_column(String(192), unique=True, index=True)
    aggregate_type: Mapped[str] = mapped_column(String(64), default="knowledge_job")
    aggregate_id: Mapped[str] = mapped_column(ForeignKey("knowledge_jobs.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(64), default="knowledge_job.requested")
    payload_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class KnowledgeRetrievalLog(Base):
    __tablename__ = "knowledge_retrieval_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    conversation_id: Mapped[str | None] = mapped_column(ForeignKey("conversations.id"), nullable=True, index=True)
    user_message_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id"), nullable=True, index=True)
    assistant_message_id: Mapped[str | None] = mapped_column(ForeignKey("messages.id"), nullable=True, index=True)
    knowledge_base_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id"), index=True)
    query: Mapped[str] = mapped_column(Text)
    retrieval_mode: Mapped[str] = mapped_column(String(32), default="vector")
    top_k: Mapped[int] = mapped_column(Integer, default=20)
    rerank_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    rerank_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    candidates_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    selected_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    diagnostics_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    sources_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="success")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    elapsed_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KnowledgeEvalSet(Base):
    __tablename__ = "knowledge_eval_sets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    knowledge_base_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id"), index=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    knowledge_base = relationship("KnowledgeBase", back_populates="eval_sets")
    cases = relationship("KnowledgeEvalCase", back_populates="eval_set", cascade="all, delete-orphan")
    runs = relationship("KnowledgeEvalRun", back_populates="eval_set", cascade="all, delete-orphan")


class KnowledgeEvalCase(Base):
    __tablename__ = "knowledge_eval_cases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    knowledge_base_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id"), index=True)
    eval_set_id: Mapped[str] = mapped_column(ForeignKey("knowledge_eval_sets.id"), index=True)
    query: Mapped[str] = mapped_column(Text)
    expected_document_id: Mapped[str | None] = mapped_column(ForeignKey("knowledge_documents.id"), nullable=True, index=True)
    # Chunk 是可重建的派生数据；重索引删除旧 Chunk 时保留评测用例，只让精确 Chunk 标注失效。
    expected_chunk_id: Mapped[str | None] = mapped_column(
        ForeignKey("knowledge_chunks.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    expected_answer_keywords_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    difficulty: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tags_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    eval_set = relationship("KnowledgeEvalSet", back_populates="cases")


class KnowledgeEvalRun(Base):
    __tablename__ = "knowledge_eval_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    knowledge_base_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id"), index=True)
    eval_set_id: Mapped[str] = mapped_column(ForeignKey("knowledge_eval_sets.id"), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    retrieval_mode: Mapped[str] = mapped_column(String(32), default="vector")
    top_k: Mapped[int] = mapped_column(Integer, default=20)
    rerank_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    metrics_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    eval_set = relationship("KnowledgeEvalSet", back_populates="runs")
    results = relationship("KnowledgeEvalResult", back_populates="run", cascade="all, delete-orphan")


class KnowledgeEvalResult(Base):
    __tablename__ = "knowledge_eval_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    knowledge_base_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("knowledge_eval_runs.id"), index=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("knowledge_eval_cases.id"), index=True)
    query: Mapped[str] = mapped_column(Text)
    retrieved_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    expected_chunk_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    hit_at_k: Mapped[bool] = mapped_column(Boolean, default=False)
    mrr: Mapped[float | None] = mapped_column(Float, nullable=True)
    context_precision: Mapped[float | None] = mapped_column(Float, nullable=True)
    context_recall: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    run = relationship("KnowledgeEvalRun", back_populates="results")
