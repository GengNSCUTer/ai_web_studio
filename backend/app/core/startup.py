from sqlalchemy import text

from app.core.database import Base, engine
from app.models import (  # noqa: F401
    Attachment,
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
    KnowledgeRetrievalLog,
    Message,
    Project,
    ProjectFile,
    PromptTemplate,
    ToolCallRun,
    ToolRouteRun,
    McpServer,
    McpTool,
    UserToolCredential,
    User,
    UserMemory,
    UserSetting,
    WorkspaceToolSetting,
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


def ensure_runtime_schema() -> None:
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

    knowledge_chunk_columns = _get_column_names("knowledge_chunks")
    if knowledge_chunk_columns and "metadata_json" not in knowledge_chunk_columns:
        statements.append("alter table knowledge_chunks add column metadata_json text")

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

    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
