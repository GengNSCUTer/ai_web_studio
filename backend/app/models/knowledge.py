from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    documents = relationship("KnowledgeDocument", back_populates="knowledge_base", cascade="all, delete-orphan")
    jobs = relationship("KnowledgeJob", back_populates="knowledge_base", cascade="all, delete-orphan")
    eval_sets = relationship("KnowledgeEvalSet", back_populates="knowledge_base", cascade="all, delete-orphan")


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

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    knowledge_base_id: Mapped[str] = mapped_column(ForeignKey("knowledge_bases.id"), index=True)
    document_id: Mapped[str] = mapped_column(ForeignKey("knowledge_documents.id"), index=True)
    chunk_index: Mapped[int] = mapped_column(Integer)
    vector_id: Mapped[int] = mapped_column(Integer, index=True)
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
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
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    knowledge_base = relationship("KnowledgeBase", back_populates="jobs")
    document = relationship("KnowledgeDocument", back_populates="jobs")


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
    expected_chunk_id: Mapped[str | None] = mapped_column(ForeignKey("knowledge_chunks.id"), nullable=True, index=True)
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
