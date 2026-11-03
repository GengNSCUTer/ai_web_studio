"""持久化工具检索及分维度ANN；不修改知识库向量列。"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import TSVECTOR
from pgvector.sqlalchemy import Vector

revision = "20261103_03"
down_revision = "20261103_02"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("tool_search_entries",
        sa.Column("scope", sa.String(64), primary_key=True),
        sa.Column("tool_key", sa.String(220), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("category", sa.String(64), nullable=False),
        sa.Column("metadata_text", sa.Text(), nullable=False),
        sa.Column("lexical_text", sa.Text(), nullable=False),
        sa.Column("search_vector", TSVECTOR(), sa.Computed("to_tsvector('simple'::regconfig, lexical_text)", persisted=True)),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("embedding", Vector(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.CheckConstraint("embedding IS NULL OR vector_dims(embedding) = dimensions", name="ck_tool_search_dimensions"))
    op.create_index("ix_tool_search_user_scope", "tool_search_entries", ["user_id", "scope"])
    op.create_index("ix_tool_search_terms", "tool_search_entries", ["search_vector"], postgresql_using="gin")
    # 维度仅来自固定迁移常量，不根据用户输入执行DDL；高维使用半精度索引。
    for dim in (256, 384, 512, 768, 1024, 1536, 2048, 3072):
        kind = "vector" if dim <= 2000 else "halfvec"
        op.execute(f"CREATE INDEX ix_tool_search_hnsw_{dim} ON tool_search_entries USING hnsw "
                   f"((embedding::{kind}({dim})) {kind}_cosine_ops) WHERE dimensions = {dim}")


def downgrade():
    op.drop_table("tool_search_entries")
