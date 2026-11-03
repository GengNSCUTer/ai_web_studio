"""保存长期记忆关系判断及审核时的版本快照。"""

from alembic import op
import sqlalchemy as sa

revision = "20261103_04"
down_revision = "20261103_03"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("user_memories", sa.Column("conflict_review_json", sa.Text(), nullable=True))


def downgrade():
    op.drop_column("user_memories", "conflict_review_json")
