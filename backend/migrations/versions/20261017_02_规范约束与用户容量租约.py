"""为用户级资源租约增加持久化容量表。"""
from alembic import op
import sqlalchemy as sa

revision = "20261017_02"
down_revision = "20261017_01"
branch_labels = None
depends_on = None


def upgrade():
    # 版本 01 已经是冻结的现有结构收编；本版本只新增配额租约，避免
    # 自动比较把历史可空列、索引或约束误判成可在线删除/修改。
    op.create_table(
        "resource_leases",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("resource", sa.String(length=24), nullable=False),
        sa.Column("reserved_bytes", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_resource_leases_user_resource_expiry",
        "resource_leases",
        ["user_id", "resource", "expires_at"],
    )


def downgrade():
    op.drop_index("ix_resource_leases_user_resource_expiry", table_name="resource_leases")
    op.drop_table("resource_leases")
