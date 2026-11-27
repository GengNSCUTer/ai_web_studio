"""长期记忆分片进度与精确冲突索引。"""

import sqlalchemy as sa
from alembic import op

revision = "20261127_04"
down_revision = "20261127_03"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("memory_extraction_jobs", sa.Column("fragment_cursor", sa.Integer(), server_default="0", nullable=False))
    op.add_column("memory_extraction_jobs", sa.Column("fragment_source_ids", sa.Text(), nullable=True))
    op.add_column("memory_extraction_jobs", sa.Column("staged_candidates", sa.JSON(), nullable=True))
    op.create_index("ix_memory_conflict_fact", "user_memories", ["user_id", "status", "fact_key", "project_id"])


def downgrade():
    op.drop_index("ix_memory_conflict_fact", table_name="user_memories")
    op.drop_column("memory_extraction_jobs", "staged_candidates")
    op.drop_column("memory_extraction_jobs", "fragment_source_ids")
    op.drop_column("memory_extraction_jobs", "fragment_cursor")
