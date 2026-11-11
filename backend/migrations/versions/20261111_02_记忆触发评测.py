"""记录触发决策并支持 buffer 的低频语义门控。"""

import sqlalchemy as sa
from alembic import op

revision = "20261111_02"
down_revision = "20261111_01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("user_settings", sa.Column("memory_semantic_trigger_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("user_settings", sa.Column("memory_semantic_last_checked_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("memory_extraction_jobs", sa.Column("semantic_action", sa.String(24), nullable=True))
    op.add_column("memory_extraction_jobs", sa.Column("semantic_confidence", sa.String(16), nullable=True))
    op.add_column("memory_extraction_jobs", sa.Column("semantic_model_called", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "memory_trigger_decisions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=True),
        sa.Column("message_id", sa.String(36), sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False),
        sa.Column("observed_turn_id", sa.String(36), sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=True),
        sa.Column("source_digest", sa.String(64), nullable=False),
        sa.Column("policy_version", sa.String(32), nullable=False, server_default="rules-v2"),
        sa.Column("action", sa.String(24), nullable=False),
        sa.Column("reason", sa.String(64), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("job_id", sa.String(36), sa.ForeignKey("memory_extraction_jobs.id", ondelete="SET NULL"), nullable=True),
        sa.Column("job_created", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("outcome", sa.String(32), nullable=False, server_default="buffered"),
        sa.Column("context_json", sa.Text(), nullable=True),
        sa.Column("semantic_action", sa.String(24), nullable=True),
        sa.Column("semantic_confidence", sa.String(16), nullable=True),
        sa.Column("semantic_model_called", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("expected_action", sa.String(24), nullable=True),
        sa.Column("review_reason", sa.String(32), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("conversation_id", "message_id", "observed_turn_id", "source_digest", "policy_version",
                           name="uq_memory_trigger_decision_source"),
    )
    op.create_index("ix_memory_trigger_decisions_conversation_created", "memory_trigger_decisions",
                   ["conversation_id", "created_at"])
    op.create_index("ix_memory_trigger_decisions_job", "memory_trigger_decisions", ["job_id"])
    op.create_index("ix_memory_trigger_decisions_user_id", "memory_trigger_decisions", ["user_id"])
    op.create_index("ix_memory_trigger_decisions_project_id", "memory_trigger_decisions", ["project_id"])


def downgrade():
    for index in ("ix_memory_trigger_decisions_project_id", "ix_memory_trigger_decisions_user_id",
                  "ix_memory_trigger_decisions_job", "ix_memory_trigger_decisions_conversation_created"):
        op.drop_index(index, table_name="memory_trigger_decisions")
    op.drop_table("memory_trigger_decisions")
    for column in ("semantic_model_called", "semantic_confidence", "semantic_action"):
        op.drop_column("memory_extraction_jobs", column)
    op.drop_column("user_settings", "memory_semantic_last_checked_at")
    op.drop_column("user_settings", "memory_semantic_trigger_enabled")
