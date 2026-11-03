"""保存每轮用户明确选择的MCP资源。"""

from alembic import op
import sqlalchemy as sa

revision = "20261103_02"
down_revision = "20261103_01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("messages", sa.Column("mcp_resources_json", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("messages", "mcp_resources_json")
