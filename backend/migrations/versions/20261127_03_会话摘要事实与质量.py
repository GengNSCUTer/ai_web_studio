"""摘要事实与旧版本质量标记。"""

from alembic import op
import sqlalchemy as sa

revision = "20261127_03"
down_revision = "20261127_02"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("messages", sa.Column("history_mode", sa.String(24), nullable=False, server_default="complete"))
    op.add_column("conversation_summary_checkpoints", sa.Column("facts", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("conversation_summary_jobs", sa.Column("partial_facts", sa.JSON(), nullable=False, server_default="[]"))
    # 从已知 fallback 派生的后续旧版本也需要从原文修复。
    op.execute("""
        WITH first_fallback AS (
            SELECT DISTINCT ON (conversation_id) conversation_id, boundary_sequence, boundary_created_at, boundary_message_id
            FROM conversation_summary_checkpoints WHERE mode = 'fallback'
            ORDER BY conversation_id, boundary_sequence, boundary_created_at, boundary_message_id
        )
        UPDATE conversation_summary_checkpoints AS checkpoint
        SET mode = CASE WHEN EXISTS (
            SELECT 1 FROM first_fallback AS degraded
            WHERE degraded.conversation_id = checkpoint.conversation_id
              AND (checkpoint.boundary_sequence, checkpoint.boundary_created_at, checkpoint.boundary_message_id)
                  >= (degraded.boundary_sequence, degraded.boundary_created_at, degraded.boundary_message_id)
        ) THEN 'fallback' ELSE 'legacy' END
    """)
    op.execute("""
        UPDATE conversation_summary_jobs
        SET partial_summary = NULL, partial_message_id = NULL, partial_offset = 0,
            status = CASE WHEN status = 'running' THEN 'queued' ELSE status END,
            lease_version = lease_version + 1, lease_owner = NULL, lease_expires_at = NULL
        WHERE status IN ('queued', 'running', 'failed') AND (status = 'running' OR partial_message_id IS NOT NULL)
    """)


def downgrade():
    op.drop_column("messages", "history_mode")
    op.execute("UPDATE conversation_summary_checkpoints SET mode = 'model' WHERE mode = 'legacy'")
    op.drop_column("conversation_summary_jobs", "partial_facts")
    op.drop_column("conversation_summary_checkpoints", "facts")
