"""保存 Chat 成功后的待消费记忆触发与会话消息序号水位。"""

import sqlalchemy as sa
from alembic import op

revision = "20261127_01"
down_revision = "20261126_01"
branch_labels = None
depends_on = None


def _invalidate_reordered_summaries():
    # 只清除顺序确实改变的会话摘要。下次 Chat 由既有摘要链路重建。
    op.execute("""
        WITH history AS (
            SELECT conversation_id, COALESCE(sequence, 0) AS position,
                   LAG(COALESCE(sequence, 0)) OVER (
                       PARTITION BY conversation_id ORDER BY created_at, sequence NULLS LAST, id
                   ) AS previous_position
            FROM messages
        ), changed AS (
            SELECT DISTINCT conversation_id FROM history WHERE position < previous_position
        ), updated AS (
            UPDATE conversations SET context_summary = NULL, context_summary_boundary_message_id = NULL,
                context_summary_updated_at = NULL, context_history_revision = context_history_revision + 1
            WHERE id IN (SELECT conversation_id FROM changed) RETURNING id
        ), removed AS (
            DELETE FROM conversation_summary_checkpoints WHERE conversation_id IN (SELECT id FROM updated)
        )
        UPDATE conversation_summary_jobs SET status = 'cancelled', partial_summary = NULL,
            lease_owner = NULL, lease_expires_at = NULL, lease_version = lease_version + 1
        WHERE conversation_id IN (SELECT id FROM updated) AND status IN ('queued', 'running', 'failed')
    """)


def upgrade():
    op.add_column("conversations", sa.Column("message_sequence_high_water", sa.Integer(),
                                           nullable=False, server_default="0"))
    op.execute("""
        UPDATE conversations AS c SET message_sequence_high_water = GREATEST(
            c.memory_scan_cursor, c.memory_extraction_cursor,
            COALESCE((SELECT max(m.sequence) FROM messages AS m WHERE m.conversation_id = c.id), 0))
    """)
    op.create_table(
        "chat_run_memory_triggers",
        sa.Column("run_id", sa.String(36), sa.ForeignKey("chat_runs.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("project_id", sa.String(36), nullable=True),
        sa.Column("memory_enabled", sa.Boolean(), nullable=False),
        sa.Column("automatic_enabled", sa.Boolean(), nullable=False),
    )
    op.create_index("ix_chat_run_memory_triggers_available_at", "chat_run_memory_triggers", ["available_at"])
    _invalidate_reordered_summaries()


def downgrade():
    _invalidate_reordered_summaries()
    op.drop_table("chat_run_memory_triggers")
    op.drop_column("conversations", "message_sequence_high_water")
