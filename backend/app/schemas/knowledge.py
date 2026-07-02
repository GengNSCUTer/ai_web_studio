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
    parent_chunk_size: int | None = Field(default=None, ge=500, le=20000)
    child_chunk_size: int | None = Field(default=None, ge=100, le=4000)
    child_chunk_overlap: int | None = Field(default=None, ge=0, le=2000)
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
    retrieval_mode: str | None = Field(default=None, max_length=32)
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


class KnowledgeCredentialResponse(BaseModel):
    provider_key: str
    credential_name: str
    is_enabled: bool
    has_api_key: bool
    api_key_masked: str | None = None
    source: str = "user"


class KnowledgeCredentialUpdate(BaseModel):
    credential_name: str | None = Field(default=None, max_length=128)
    api_key: str | None = None
    clear_api_key: bool | None = None
    is_enabled: bool | None = None


class KnowledgeConnectionTestResponse(BaseModel):
    ok: bool
    provider_key: str
    message: str


class KnowledgeDocumentParseResponse(BaseModel):
    document: KnowledgeDocumentResponse
    job: KnowledgeJobResponse
    markdown_preview: str | None = None


class KnowledgeMarkdownChunkResponse(BaseModel):
    chunk_id: str
    chunk_index: int
    source_start: int | None = None
    source_end: int | None = None
    content: str


class KnowledgeMarkdownPreviewResponse(BaseModel):
    document_id: str
    file_name: str
    markdown: str
    chunks: list[KnowledgeMarkdownChunkResponse] = Field(default_factory=list)


class KnowledgeDocumentIndexResponse(BaseModel):
    document: KnowledgeDocumentResponse
    job: KnowledgeJobResponse
    chunk_count: int
    index_path: str | None = None


class KnowledgeRetrievalTestRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    top_k: int | None = Field(default=None, ge=1, le=50)
    document_ids: list[str] = Field(default_factory=list)
    file_types: list[str] = Field(default_factory=list)
    page_start: int | None = Field(default=None, ge=1)
    page_end: int | None = Field(default=None, ge=1)
    section_query: str | None = Field(default=None, max_length=128)


class KnowledgeRetrievalChunkResponse(BaseModel):
    chunk_id: str
    document_id: str
    file_name: str
    chunk_index: int
    score: float
    vector_score: float
    rerank_score: float | None = None
    rank_source: str = "vector"
    content: str
    metadata: dict | None = None


class KnowledgeRetrievalTestResponse(BaseModel):
    query: str
    top_k: int
    total_chunks: int
    rerank_enabled: bool = False
    rerank_model: str | None = None
    filters: dict = Field(default_factory=dict)
    results: list[KnowledgeRetrievalChunkResponse]


class KnowledgeRetrievalLogResponse(BaseModel):
    id: str
    user_id: str
    conversation_id: str | None = None
    user_message_id: str | None = None
    assistant_message_id: str | None = None
    knowledge_base_id: str
    query: str
    retrieval_mode: str
    top_k: int
    rerank_enabled: bool
    rerank_model: str | None = None
    candidates: list[dict] = Field(default_factory=list)
    selected: list[dict] = Field(default_factory=list)
    diagnostics: dict = Field(default_factory=dict)
    sources: list[dict] = Field(default_factory=list)
    status: str
    error_message: str | None = None
    elapsed_ms: int | None = None
    created_at: datetime


class KnowledgeEvalSetCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str | None = None


class KnowledgeEvalSetResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    knowledge_base_id: str
    name: str
    description: str | None = None
    created_at: datetime
    updated_at: datetime | None = None


class KnowledgeEvalCaseCreate(BaseModel):
    query: str = Field(min_length=1, max_length=2000)
    expected_document_id: str | None = Field(default=None, max_length=36)
    expected_chunk_id: str | None = Field(default=None, max_length=36)
    expected_answer_keywords: list[str] = Field(default_factory=list)
    difficulty: str | None = Field(default=None, max_length=32)
    tags: list[str] = Field(default_factory=list)


class KnowledgeEvalCaseResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    knowledge_base_id: str
    eval_set_id: str
    query: str
    expected_document_id: str | None = None
    expected_chunk_id: str | None = None
    expected_answer_keywords: list[str] = Field(default_factory=list)
    difficulty: str | None = None
    tags: list[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime | None = None


class KnowledgeEvalRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    knowledge_base_id: str
    eval_set_id: str
    status: str
    retrieval_mode: str
    top_k: int
    rerank_enabled: bool
    metrics: dict = Field(default_factory=dict)
    error_message: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    created_at: datetime


class KnowledgeEvalResultResponse(BaseModel):
    id: str
    user_id: str
    knowledge_base_id: str
    run_id: str
    case_id: str
    query: str
    retrieved: list[dict] = Field(default_factory=list)
    expected_document_id: str | None = None
    expected_chunk_id: str | None = None
    hit_at_k: bool
    mrr: float | None = None
    context_precision: float | None = None
    context_recall: float | None = None
    created_at: datetime


class KnowledgeEvalOutcomeResponse(BaseModel):
    run: KnowledgeEvalRunResponse
    results: list[KnowledgeEvalResultResponse] = Field(default_factory=list)


class KnowledgeEvalRunRequest(BaseModel):
    top_k: int | None = Field(default=None, ge=1, le=50)
