"""摘要安全版本和独立重建任务。"""

import sqlalchemy as sa
from alembic import op

revision = "20261117_01"
down_revision = "20261111_02"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("conversations", sa.Column("context_history_revision", sa.Integer(), nullable=False, server_default="0"))
    op.create_table("conversation_summary_checkpoints",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("summary_text", sa.Text(), nullable=False),
        sa.Column("boundary_message_id", sa.String(36), nullable=False),
        sa.Column("boundary_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("boundary_sequence", sa.Integer(), nullable=False),
        sa.Column("source_revision", sa.Integer(), nullable=False),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_summary_checkpoint_order", "conversation_summary_checkpoints",
        ["conversation_id", "boundary_created_at", "boundary_sequence", "boundary_message_id"])
    op.create_table("conversation_summary_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_revision", sa.Integer(), nullable=False),
        sa.Column("target_message_id", sa.String(36), nullable=False),
        sa.Column("cursor_message_id", sa.String(36)),
        sa.Column("partial_message_id", sa.String(36)),
        sa.Column("partial_offset", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("partial_summary", sa.Text()),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("batches", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_owner", sa.String(128)),
        sa.Column("lease_version", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_summary_job_claim", "conversation_summary_jobs", ["status", "next_attempt_at", "created_at"])
    op.create_index("ix_summary_job_conversation", "conversation_summary_jobs", ["conversation_id", "source_revision"])


def downgrade():
    op.drop_table("conversation_summary_jobs")
    op.drop_table("conversation_summary_checkpoints")
    op.drop_column("conversations", "context_history_revision")
