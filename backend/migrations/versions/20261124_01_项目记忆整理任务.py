"""项目记忆整理任务及审批结果。"""

from alembic import op
import sqlalchemy as sa

revision = "20261124_01"
down_revision = "20261118_01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("user_memories", sa.Column("needs_explicit_choice", sa.Boolean(),
                                            nullable=False, server_default=sa.false()))
    op.create_table(
        "memory_maintenance_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("lease_version", sa.Integer(), nullable=False),
        sa.Column("lease_owner", sa.String(128)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("available_at", sa.DateTime(timezone=True)),
        sa.Column("input_versions", sa.JSON(), nullable=False),
        sa.Column("proposals", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(64)),
        sa.Column("model_ms", sa.Integer()),
        sa.Column("estimated_input_tokens", sa.Integer()),
        sa.Column("estimated_output_tokens", sa.Integer()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_memory_maintenance_claim", "memory_maintenance_jobs", ["status", "available_at", "created_at"])
    op.create_index("ix_memory_maintenance_project", "memory_maintenance_jobs", ["user_id", "project_id", "created_at"])


def downgrade():
    op.drop_table("memory_maintenance_jobs")
    op.drop_column("user_memories", "needs_explicit_choice")
