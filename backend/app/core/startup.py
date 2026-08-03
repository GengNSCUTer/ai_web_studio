from contextlib import contextmanager
from collections.abc import Iterator

from sqlalchemy import text

from app.core.database import Base, engine
from app.models import (  # noqa: F401
    Attachment,
    AgentApproval,
    AgentArtifact,
    AgentCheckpoint,
    AgentOutboxEvent,
    AgentRun,
    AgentStep,
    FileRevision,
    PatchDraft,
    Conversation,
    ConversationShare,
    KnowledgeBase,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeEvalCase,
    KnowledgeEvalResult,
    KnowledgeEvalRun,
    KnowledgeEvalSet,
    KnowledgeJob,
    KnowledgeIndexGeneration,
    KnowledgeRetrievalLog,
    OutboxEvent,
    Message,
    ChatRuntimeMetric,
    Project,
    ProjectFile,
    PromptTemplate,
    ToolCallRun,
    ToolRouteRun,
    McpServer,
    McpTool,
    SkillInstallationRevision,
    UserToolCredential,
    UserSkillInstallation,
    User,
    UserMemory,
    MemoryExtractionJob,
    UserSetting,
    WorkspaceToolSetting,
    WorkspaceAgentPolicy,
)


def _get_column_names(table_name: str) -> set[str]:
    query = text(
        """
        select column_name
        from information_schema.columns
        where table_schema = 'public' and table_name = :table_name
        """
    )
    with engine.begin() as connection:
        result = connection.execute(query, {"table_name": table_name})
        return {row[0] for row in result}


def _get_column_metadata(table_name: str, column_name: str) -> tuple[str, str | None] | None:
    """Return nullability and default for an existing PostgreSQL column.

    Runtime DDL can be interrupted between statements. Reading this metadata lets
    the next startup finish a partially applied migration without repeating an
    expensive ``SET NOT NULL`` scan on every healthy boot.
    """
    query = text(
        """
        select is_nullable, column_default
        from information_schema.columns
        where table_schema = 'public'
          and table_name = :table_name
          and column_name = :column_name
        limit 1
        """
    )
    with engine.begin() as connection:
        row = connection.execute(
            query,
            {"table_name": table_name, "column_name": column_name},
        ).first()
        if not row:
            return None
        return str(row[0]), str(row[1]) if row[1] is not None else None


def _generation_id_migration_statements(
    message_columns: set[str],
    generation_metadata: tuple[str, str | None] | None,
) -> list[str]:
    """Build an idempotent generation_id migration, including interrupted states."""
    statements: list[str] = []
    column_missing = "generation_id" not in message_columns
    metadata_unknown = generation_metadata is None
    nullable = metadata_unknown or generation_metadata[0].upper() == "YES"
    default_missing = metadata_unknown or generation_metadata[1] is None

    if column_missing:
        # Existing rows receive a distinct token before the column becomes NOT NULL;
        # md5 avoids requiring a PostgreSQL UUID extension during in-place upgrades.
        statements.append("alter table messages add column generation_id varchar(36)")
    if column_missing or nullable:
        # This also completes a migration interrupted after ADD COLUMN but before
        # backfill/default/NOT NULL were applied.
        statements.append(
            "update messages set generation_id = md5(random()::text || clock_timestamp()::text || id) "
            "where generation_id is null"
        )
    if default_missing:
        statements.append(
            "alter table messages alter column generation_id "
            "set default md5(random()::text || clock_timestamp()::text)"
        )
    if nullable:
        statements.append("alter table messages alter column generation_id set not null")
    return statements


def _get_index_names(table_name: str) -> set[str]:
    query = text(
        """
        select indexname
        from pg_indexes
        where schemaname = 'public' and tablename = :table_name
        """
    )
    with engine.begin() as connection:
        result = connection.execute(query, {"table_name": table_name})
        return {row[0] for row in result}


def _get_foreign_key(table_name: str, column_name: str) -> tuple[str, str] | None:
    query = text(
        """
        select kcu.constraint_name, rc.delete_rule
        from information_schema.key_column_usage as kcu
        join information_schema.referential_constraints as rc
          on rc.constraint_schema = kcu.constraint_schema
         and rc.constraint_name = kcu.constraint_name
        where kcu.table_schema = 'public'
          and kcu.table_name = :table_name
          and kcu.column_name = :column_name
        limit 1
        """
    )
    with engine.begin() as connection:
        row = connection.execute(
            query,
            {"table_name": table_name, "column_name": column_name},
        ).first()
        return (str(row[0]), str(row[1])) if row else None


def _ensure_pgvector_extension() -> None:
    """Install the vector SQL type before metadata or runtime DDL refers to it."""
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as connection:
        connection.execute(text("create extension if not exists vector"))


@contextmanager
def _runtime_schema_lock() -> Iterator[None]:
    """Serialize startup DDL across PostgreSQL application instances.

    The lock is held on a dedicated connection for the entire schema check and
    DDL sequence. Other instances block before inspecting metadata, so a stale
    "column missing" observation cannot lead to duplicate ALTER statements.
    SQLite and other development backends retain the existing no-op behavior.
    """

    if engine.dialect.name != "postgresql":
        yield
        return

    lock_key = "ai_web_studio:runtime_schema"
    with engine.connect() as connection:
        connection.execute(
            text("select pg_advisory_lock(hashtext(:lock_key))"),
            {"lock_key": lock_key},
        )
        try:
            yield
        finally:
            connection.execute(
                text("select pg_advisory_unlock(hashtext(:lock_key))"),
                {"lock_key": lock_key},
            )


def ensure_runtime_schema() -> None:
    with _runtime_schema_lock():
        _ensure_runtime_schema_unlocked()


def _ensure_runtime_schema_unlocked() -> None:
    _ensure_pgvector_extension()
    Base.metadata.create_all(bind=engine)

    columns = _get_column_names("user_settings")
    user_indexes = _get_index_names("users")

    statements: list[str] = []
    if "ux_users_email_lower" not in user_indexes:
        statements.append("create unique index if not exists ux_users_email_lower on users (lower(email))")
    if "ux_users_username_lower" not in user_indexes:
        statements.append("create unique index if not exists ux_users_username_lower on users (lower(username))")

    if "provider_type" not in columns:
        statements.append(
            "alter table user_settings add column provider_type varchar(32) default 'ollama'"
        )
    if "api_key" not in columns:
        statements.append("alter table user_settings add column api_key text")
    if "api_base_url" not in columns:
        statements.append(
            "alter table user_settings add column api_base_url varchar(255) default 'https://api.siliconflow.cn/v1'"
        )
        statements.append(
            """
            update user_settings
            set api_base_url = ollama_base_url
            where provider_type = 'openai-compatible'
              and ollama_base_url is not null
              and ollama_base_url <> ''
              and ollama_base_url <> 'http://127.0.0.1:11435'
            """
        )
        statements.append(
            """
            update user_settings
            set ollama_base_url = 'http://127.0.0.1:11435'
            where provider_type = 'openai-compatible'
              and ollama_base_url is not null
              and ollama_base_url <> ''
              and ollama_base_url <> 'http://127.0.0.1:11435'
            """
        )
    if "model_context_window" not in columns:
        statements.append("alter table user_settings add column model_context_window integer default 128000")
    if "context_mode" not in columns:
        statements.append("alter table user_settings add column context_mode varchar(32) default 'balanced'")
    if "memory_enabled" not in columns:
        statements.append("alter table user_settings add column memory_enabled boolean default true")
    if "memory_max_chars" not in columns:
        statements.append("alter table user_settings add column memory_max_chars integer default 4000")
    if "memory_auto_candidate_enabled" not in columns:
        statements.append(
            "alter table user_settings add column memory_auto_candidate_enabled boolean default false"
        )
    if "memory_auto_candidate_turn_interval" not in columns:
        statements.append(
            "alter table user_settings add column memory_auto_candidate_turn_interval integer default 4"
        )

    memory_columns = _get_column_names("user_memories")
    memory_additions = {
        "status": "varchar(24) not null default 'active'",
        "project_id": "varchar(36)",
        "importance": "double precision not null default 0.5",
        "sensitivity": "varchar(24) not null default 'normal'",
        "risk_level": "varchar(32) not null default 'safe'",
        "candidate_reason": "text",
        "content_hash": "varchar(64)",
        "supersedes_memory_id": "varchar(36)",
        "expires_at": "timestamptz",
        "review_at": "timestamptz",
    }
    for column_name, column_type in memory_additions.items():
        if memory_columns and column_name not in memory_columns:
            statements.append(f"alter table user_memories add column {column_name} {column_type}")

    approval_columns = _get_column_names("agent_approvals")
    if approval_columns and "decision_mode" not in approval_columns:
        statements.append(
            "alter table agent_approvals add column decision_mode varchar(32) "
            "not null default 'user_confirmation'"
        )
    agent_run_columns = _get_column_names("agent_runs")
    if agent_run_columns and "runtime_kind" not in agent_run_columns:
        statements.append(
            "alter table agent_runs add column runtime_kind varchar(48) not null default 'file_edit'"
        )
    agent_step_columns = _get_column_names("agent_steps")
    agent_step_additions = {
        "result_bindings_json": "text not null default '[]'",
        "available_at": "timestamptz",
        "max_attempts": "integer not null default 3",
        "heartbeat_at": "timestamptz",
        "dead_lettered_at": "timestamptz",
    }
    for column_name, column_type in agent_step_additions.items():
        if agent_step_columns and column_name not in agent_step_columns:
            statements.append(f"alter table agent_steps add column {column_name} {column_type}")
    if "ui_language" not in columns:
        statements.append("alter table user_settings add column ui_language varchar(16) default 'zh-CN'")
    if "theme_mode" not in columns:
        statements.append("alter table user_settings add column theme_mode varchar(16) default 'system'")
    if "knowledge_parser_provider" not in columns:
        statements.append("alter table user_settings add column knowledge_parser_provider varchar(32) default 'local_basic'")
    if "knowledge_embedding_provider" not in columns:
        statements.append("alter table user_settings add column knowledge_embedding_provider varchar(32) default 'siliconflow'")
    if "knowledge_embedding_base_url" not in columns:
        statements.append(
            "alter table user_settings add column knowledge_embedding_base_url varchar(255) default 'https://api.siliconflow.cn/v1'"
        )
    if "knowledge_embedding_model" not in columns:
        statements.append("alter table user_settings add column knowledge_embedding_model varchar(128) default 'BAAI/bge-m3'")
    if "knowledge_embedding_dimensions" not in columns:
        statements.append("alter table user_settings add column knowledge_embedding_dimensions integer default 1024")
    if "knowledge_rerank_enabled" not in columns:
        statements.append("alter table user_settings add column knowledge_rerank_enabled boolean default true")
    if "knowledge_rerank_provider" not in columns:
        statements.append("alter table user_settings add column knowledge_rerank_provider varchar(32) default 'siliconflow'")
    if "knowledge_rerank_base_url" not in columns:
        statements.append(
            "alter table user_settings add column knowledge_rerank_base_url varchar(255) default 'https://api.siliconflow.cn/v1'"
        )
    if "knowledge_rerank_model" not in columns:
        statements.append(
            "alter table user_settings add column knowledge_rerank_model varchar(128) default 'BAAI/bge-reranker-v2-m3'"
        )
    if "knowledge_embedding_api_key" not in columns:
        statements.append("alter table user_settings add column knowledge_embedding_api_key text")
    if "knowledge_rerank_api_key" not in columns:
        statements.append("alter table user_settings add column knowledge_rerank_api_key text")

    knowledge_base_columns = _get_column_names("knowledge_bases")
    if knowledge_base_columns and "active_index_generation" not in knowledge_base_columns:
        statements.append(
            "alter table knowledge_bases add column active_index_generation varchar(64) not null default 'legacy'"
        )

    knowledge_job_columns = _get_column_names("knowledge_jobs")
    if knowledge_job_columns and "error_code" not in knowledge_job_columns:
        statements.append("alter table knowledge_jobs add column error_code varchar(64)")
    knowledge_job_additions = {
        "idempotency_key": "varchar(192)",
        "result_json": "text",
        "max_attempts": "integer not null default 3",
        "available_at": "timestamptz",
        "lease_owner": "varchar(128)",
        "lease_expires_at": "timestamptz",
        "lease_version": "integer not null default 0",
        "heartbeat_at": "timestamptz",
        "dead_lettered_at": "timestamptz",
    }
    for column_name, column_type in knowledge_job_additions.items():
        if knowledge_job_columns and column_name not in knowledge_job_columns:
            statements.append(f"alter table knowledge_jobs add column {column_name} {column_type}")
    knowledge_job_indexes = _get_index_names("knowledge_jobs")
    if not {"ux_knowledge_jobs_idempotency_key", "ix_knowledge_jobs_idempotency_key"}.intersection(
        knowledge_job_indexes
    ):
        statements.append(
            "create unique index if not exists ux_knowledge_jobs_idempotency_key "
            "on knowledge_jobs (idempotency_key) where idempotency_key is not null"
        )

    generation_columns = _get_column_names("knowledge_index_generations")
    if generation_columns and "job_id" not in generation_columns:
        statements.append("alter table knowledge_index_generations add column job_id varchar(36)")
    if generation_columns and "chunk_count" not in generation_columns:
        statements.append("alter table knowledge_index_generations add column chunk_count integer")

    knowledge_chunk_columns = _get_column_names("knowledge_chunks")
    if knowledge_chunk_columns and "metadata_json" not in knowledge_chunk_columns:
        statements.append("alter table knowledge_chunks add column metadata_json text")
    if knowledge_chunk_columns and "index_generation" not in knowledge_chunk_columns:
        statements.append(
            "alter table knowledge_chunks add column index_generation varchar(64) not null default 'legacy'"
        )
    if knowledge_chunk_columns and "embedding" not in knowledge_chunk_columns:
        # 使用不限维度的 vector，保留知识库级模型配置能力。
        # legacy 行先保持 null，后续由显式 backfill 任务填充，不在启动时调外部 API。
        statements.append("alter table knowledge_chunks add column embedding vector")
    if knowledge_chunk_columns and "embedding_provider" not in knowledge_chunk_columns:
        statements.append("alter table knowledge_chunks add column embedding_provider varchar(32)")
    if knowledge_chunk_columns and "embedding_model" not in knowledge_chunk_columns:
        statements.append("alter table knowledge_chunks add column embedding_model varchar(128)")
    if knowledge_chunk_columns and "embedding_dimensions" not in knowledge_chunk_columns:
        statements.append("alter table knowledge_chunks add column embedding_dimensions integer")
    if knowledge_chunk_columns and "embedding_version" not in knowledge_chunk_columns:
        statements.append("alter table knowledge_chunks add column embedding_version varchar(32)")
    knowledge_chunk_indexes = _get_index_names("knowledge_chunks")
    if "uq_knowledge_chunks_generation_vector" not in knowledge_chunk_indexes:
        statements.append(
            """
            create unique index uq_knowledge_chunks_generation_vector
            on knowledge_chunks (knowledge_base_id, index_generation, vector_id)
            """
        )

    expected_chunk_fk = _get_foreign_key("knowledge_eval_cases", "expected_chunk_id")
    if not expected_chunk_fk or expected_chunk_fk[1].upper() != "SET NULL":
        # 历史评测用例可能只保存了 Chunk 目标；先回填所属文档，再允许重索引置空旧 Chunk 引用。
        statements.append(
            """
            update knowledge_eval_cases as eval_case
            set expected_document_id = chunk.document_id
            from knowledge_chunks as chunk
            where eval_case.expected_chunk_id = chunk.id
              and eval_case.expected_document_id is null
            """
        )
        if expected_chunk_fk:
            # 约束名来自 PostgreSQL catalog；双引号转义后再用于 DDL 标识符。
            constraint_name = expected_chunk_fk[0].replace('"', '""')
            statements.append(
                f'alter table knowledge_eval_cases drop constraint "{constraint_name}"'
            )
        statements.append(
            """
            alter table knowledge_eval_cases
            add constraint knowledge_eval_cases_expected_chunk_id_fkey
            foreign key (expected_chunk_id) references knowledge_chunks(id)
            on delete set null
            """
        )

    conversation_columns = _get_column_names("conversations")
    if "project_id" not in conversation_columns:
        statements.append("alter table conversations add column project_id varchar(36)")
    if "context_summary" not in conversation_columns:
        statements.append("alter table conversations add column context_summary text")
    if "context_summary_boundary_message_id" not in conversation_columns:
        statements.append("alter table conversations add column context_summary_boundary_message_id varchar(36)")
    if "context_summary_updated_at" not in conversation_columns:
        statements.append("alter table conversations add column context_summary_updated_at timestamptz")
    if "last_prompt_prefix_hash" not in conversation_columns:
        statements.append("alter table conversations add column last_prompt_prefix_hash varchar(64)")
    if "last_prompt_prefix_token_count" not in conversation_columns:
        statements.append("alter table conversations add column last_prompt_prefix_token_count integer")
    if "is_pinned" not in conversation_columns:
        statements.append("alter table conversations add column is_pinned boolean default false")

    message_columns = _get_column_names("messages")
    if "reasoning_content" not in message_columns:
        statements.append("alter table messages add column reasoning_content text")
    if "external_sources" not in message_columns:
        statements.append("alter table messages add column external_sources text")
    if "sequence" not in message_columns:
        statements.append("alter table messages add column sequence integer")
    generation_metadata = (
        _get_column_metadata("messages", "generation_id")
        if "generation_id" in message_columns
        else None
    )
    statements.extend(_generation_id_migration_statements(message_columns, generation_metadata))

    memory_columns = _get_column_names("user_memories")
    if "source_conversation_id" not in memory_columns:
        statements.append("alter table user_memories add column source_conversation_id varchar(36)")
    if "source_message_ids" not in memory_columns:
        statements.append("alter table user_memories add column source_message_ids text")
    if "confidence" not in memory_columns:
        statements.append("alter table user_memories add column confidence varchar(16)")

    prompt_template_columns = _get_column_names("prompt_templates")
    if prompt_template_columns and "project_id" not in prompt_template_columns:
        statements.append("alter table prompt_templates add column project_id varchar(36)")
    if prompt_template_columns and "category" not in prompt_template_columns:
        statements.append("alter table prompt_templates add column category varchar(64)")
    if prompt_template_columns and "variables" not in prompt_template_columns:
        statements.append("alter table prompt_templates add column variables text")

    mcp_tool_columns = _get_column_names("mcp_tools")
    if mcp_tool_columns and "risk_reviewed" not in mcp_tool_columns:
        # 历史动态 MCP 工具也必须重新经过人工风险审核，不能沿用远端 readOnlyHint 推导出的低风险状态。
        statements.append("alter table mcp_tools add column risk_reviewed boolean not null default false")
        statements.append(
            "update mcp_tools set is_enabled = false, read_only = false, risk_level = 'high'"
        )

    mcp_server_columns = _get_column_names("mcp_servers")
    if mcp_server_columns and "project_id" not in mcp_server_columns:
        # MCP Server 作用域是权限边界的一部分。create_all 只补新表，不能为
        # 已有部署自动补列，因此这里保持升级路径幂等。
        statements.append("alter table mcp_servers add column project_id varchar(36)")
    mcp_server_indexes = _get_index_names("mcp_servers")
    if mcp_server_columns and "ix_mcp_servers_project_id" not in mcp_server_indexes:
        statements.append("create index if not exists ix_mcp_servers_project_id on mcp_servers (project_id)")

    skill_installation_columns = _get_column_names("user_skill_installations")
    if skill_installation_columns and "manifest_digest" not in skill_installation_columns:
        statements.append("alter table user_skill_installations add column manifest_digest varchar(64)")

    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
