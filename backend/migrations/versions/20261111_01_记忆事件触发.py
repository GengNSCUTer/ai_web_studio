"""记忆扫描与提取游标分离，持久化缓冲及控制请求。"""

import sqlalchemy as sa
from alembic import op

revision = "20261111_01"
down_revision = "20261108_03"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("conversations", sa.Column("memory_scan_cursor", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("conversations", sa.Column("memory_trigger_state", sa.Text(), nullable=True))
    op.execute("UPDATE conversations SET memory_scan_cursor = memory_extraction_cursor")
    op.add_column("memory_extraction_jobs", sa.Column("trigger_action", sa.String(24), nullable=False, server_default="extract_now"))
    op.add_column("memory_extraction_jobs", sa.Column("trigger_reason", sa.String(64), nullable=False, server_default="legacy_batch"))
    op.add_column("memory_extraction_jobs", sa.Column("priority", sa.Integer(), nullable=False, server_default="0"))
    op.add_column("memory_extraction_jobs", sa.Column("control_result", sa.Text(), nullable=True))


def downgrade():
    for name in ("control_result", "priority", "trigger_reason", "trigger_action"):
        op.drop_column("memory_extraction_jobs", name)
    op.drop_column("conversations", "memory_trigger_state")
    op.drop_column("conversations", "memory_scan_cursor")
