"""记忆原生向量/词法索引、短版本标记与范围失效。"""

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import TSVECTOR

from app.core.memory_search_schema import ANN_DIMENSIONS, install_memory_search

revision = "20261105_02"
down_revision = "20261105_01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("users", sa.Column("memory_revision", sa.BigInteger(), nullable=False, server_default="0"))
    op.add_column("user_memories", sa.Column("search_terms", sa.Text(), nullable=True))
    op.add_column("user_memories", sa.Column("search_vector", TSVECTOR(),
        sa.Computed("to_tsvector('simple'::regconfig, coalesce(search_terms, ''))", persisted=True)))
    op.add_column("user_memories", sa.Column("search_embedding", Vector(), nullable=True))
    op.add_column("user_memories", sa.Column("search_dimensions", sa.Integer(), nullable=True))
    op.add_column("user_memories", sa.Column("embedding_retry_after", sa.DateTime(timezone=True), nullable=True))
    install_memory_search(op.get_bind())
    # 保留既有正文/版本/更新时间；触发器验证哈希后才能转换旧 JSON 向量。
    op.execute("UPDATE user_memories SET embedding_vector = embedding_vector")
    op.create_index("ix_memory_expiry_lookup", "user_memories", ["user_id", "status", "is_enabled", "expires_at"])
    op.create_index("ix_memory_terms", "user_memories", ["search_vector"], postgresql_using="gin")
    op.create_index("ix_memory_index_backlog", "user_memories", ["updated_at", "id"], postgresql_where=sa.text(
        "search_embedding IS NULL AND status = 'active' AND is_enabled = true "
        "AND memory_type NOT IN ('profile', 'instruction')"))
    for dim in ANN_DIMENSIONS:
        kind = "vector" if dim <= 2000 else "halfvec"
        op.execute(f"CREATE INDEX ix_memory_hnsw_{dim} ON user_memories USING hnsw "
                   f"((search_embedding::{kind}({dim})) {kind}_cosine_ops) WHERE search_dimensions = {dim}")


def downgrade():
    for table, names in {
        "user_memories": ("aiws_memory_prepare", "aiws_memory_revision"),
        "conversations": ("aiws_memory_source_move", "aiws_memory_source_delete"),
        "projects": ("aiws_memory_project_move", "aiws_memory_project_delete"),
        "user_settings": ("aiws_memory_model",),
    }.items():
        for name in names:
            op.execute(f"DROP TRIGGER IF EXISTS {name} ON {table}")
    for name in ("aiws_memory_prepare_search", "aiws_memory_bump_revision", "aiws_memory_scope_revision", "aiws_memory_reset_model_index"):
        op.execute(f"DROP FUNCTION IF EXISTS {name}()")
    for dim in ANN_DIMENSIONS:
        op.drop_index(f"ix_memory_hnsw_{dim}", table_name="user_memories")
    op.drop_index("ix_memory_terms", table_name="user_memories")
    op.drop_index("ix_memory_index_backlog", table_name="user_memories")
    op.drop_index("ix_memory_expiry_lookup", table_name="user_memories")
    for name in ("embedding_retry_after", "search_dimensions", "search_embedding", "search_vector", "search_terms"):
        op.drop_column("user_memories", name)
    op.drop_column("users", "memory_revision")
