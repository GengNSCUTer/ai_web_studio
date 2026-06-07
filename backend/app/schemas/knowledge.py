from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None
    project_id: str | None = Field(default=None, max_length=36)
    parser_provider: str = Field(default="local_basic", max_length=32)
    chunk_mode: str = Field(default="general", max_length=32)
    chunk_size: int = Field(default=1000, ge=100, le=8000)
    chunk_overlap: int = Field(default=150, ge=0, le=2000)
    chunk_delimiter: str = Field(default="\n\n", max_length=64)
    embedding_provider: str = Field(default="siliconflow", max_length=32)
    embedding_model: str = Field(default="BAAI/bge-m3", max_length=128)
    embedding_dimensions: int = Field(default=1024, ge=128, le=4096)
    rerank_enabled: bool = True
    rerank_provider: str = Field(default="siliconflow", max_length=32)
    rerank_model: str = Field(default="BAAI/bge-reranker-v2-m3", max_length=128)
    retrieval_mode: str = Field(default="vector", max_length=32)
    retrieval_top_k: int = Field(default=20, ge=1, le=100)
    rerank_top_n: int = Field(default=6, ge=1, le=50)
    score_threshold: float = Field(default=0.2, ge=0, le=1)
    max_context_chunks: int = Field(default=6, ge=1, le=50)
    max_context_chars: int = Field(default=12000, ge=1000, le=200000)
    strict_knowledge_answer: bool = False


class KnowledgeBaseUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = None
    project_id: str | None = Field(default=None, max_length=36)
    retrieval_top_k: int | None = Field(default=None, ge=1, le=100)
    rerank_top_n: int | None = Field(default=None, ge=1, le=50)
    score_threshold: float | None = Field(default=None, ge=0, le=1)
    max_context_chunks: int | None = Field(default=None, ge=1, le=50)
    max_context_chars: int | None = Field(default=None, ge=1000, le=200000)
    strict_knowledge_answer: bool | None = None


class KnowledgeBaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    project_id: str | None = None
    name: str
    description: str | None = None
    visibility: str
    parser_provider: str
    chunk_mode: str
    chunk_size: int
    chunk_overlap: int
    chunk_delimiter: str
    parent_chunk_size: int | None = None
    child_chunk_size: int | None = None
    child_chunk_overlap: int | None = None
    embedding_provider: str
    embedding_model: str
    embedding_dimensions: int
    rerank_enabled: bool
    rerank_provider: str
    rerank_model: str
    retrieval_mode: str
    retrieval_top_k: int
    rerank_top_n: int
    score_threshold: float
    max_context_chunks: int
    max_context_chars: int
    strict_knowledge_answer: bool
    document_count: int = 0
    created_at: datetime
    updated_at: datetime | None = None


class KnowledgeDocumentCreate(BaseModel):
    file_name: str = Field(min_length=1, max_length=255)
    mime_type: str | None = Field(default=None, max_length=128)
    file_size: int | None = Field(default=None, ge=0)
    storage_key: str = Field(min_length=1)


class KnowledgeDocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    knowledge_base_id: str
    user_id: str
    project_id: str | None = None
    file_name: str
    mime_type: str | None = None
    file_size: int | None = None
    storage_key: str
    parser_provider: str
    parse_status: str
    index_status: str
    document_version: int
    content_hash: str | None = None
    parsed_markdown_path: str | None = None
    parsed_assets_json: str | None = None
    error_message: str | None = None
    created_at: datetime
    updated_at: datetime | None = None


class KnowledgeJobResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    knowledge_base_id: str
    document_id: str | None = None
    job_type: str
    status: str
    payload_json: str | None = None
    retry_count: int
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime
    updated_at: datetime | None = None
