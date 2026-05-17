from sqlalchemy import text

from app.core.database import Base, engine
from app.models import Attachment, Conversation, Message, PromptTemplate, User, UserMemory, UserSetting  # noqa: F401


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


def ensure_runtime_schema() -> None:
    Base.metadata.create_all(bind=engine)

    columns = _get_column_names("user_settings")

    statements: list[str] = []
    if "provider_type" not in columns:
        statements.append(
            "alter table user_settings add column provider_type varchar(32) default 'ollama'"
        )
    if "api_key" not in columns:
        statements.append("alter table user_settings add column api_key text")
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

    conversation_columns = _get_column_names("conversations")
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

    memory_columns = _get_column_names("user_memories")
    if "source_conversation_id" not in memory_columns:
        statements.append("alter table user_memories add column source_conversation_id varchar(36)")
    if "source_message_ids" not in memory_columns:
        statements.append("alter table user_memories add column source_message_ids text")
    if "confidence" not in memory_columns:
        statements.append("alter table user_memories add column confidence varchar(16)")

    if not statements:
        return

    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
