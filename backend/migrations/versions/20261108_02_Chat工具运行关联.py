"""普通Chat工具Run关联；复用既有Agent表与原生PG图数据。"""

from alembic import op
import sqlalchemy as sa

revision = "20261108_02"
down_revision = "20261108_01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("agent_runs", sa.Column("chat_run_id", sa.String(36), nullable=True))
    op.create_foreign_key("fk_agent_run_chat_run", "agent_runs", "chat_runs", ["chat_run_id"], ["id"], ondelete="CASCADE")
    op.create_index("ix_agent_runs_chat_run_id", "agent_runs", ["chat_run_id"], unique=True)


def downgrade():
    op.drop_index("ix_agent_runs_chat_run_id", table_name="agent_runs")
    op.drop_constraint("fk_agent_run_chat_run", "agent_runs", type_="foreignkey")
    op.drop_column("agent_runs", "chat_run_id")
