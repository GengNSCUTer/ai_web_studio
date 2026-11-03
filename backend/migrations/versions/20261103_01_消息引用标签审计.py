"""消息保存线上引用审计；旧记录保持未审计，不回填为通过。"""

from alembic import op
import sqlalchemy as sa

revision = "20261103_01"
down_revision = "20261028_01"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("messages", sa.Column("citation_audit", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("messages", "citation_audit")
