"""为会话内已分配的消息序号增加唯一约束。"""

from alembic import op
from sqlalchemy import text

revision = "20261126_01"
down_revision = "20261124_01"
branch_labels = None
depends_on = None


def upgrade():
    duplicate = op.get_bind().scalar(text("""
        SELECT 1 FROM messages WHERE sequence IS NOT NULL
        GROUP BY conversation_id, sequence HAVING count(*) > 1 LIMIT 1
    """))
    if duplicate:
        raise RuntimeError("消息存在重复会话序号。请停写并核对受影响会话的顺序、摘要和记忆游标后再迁移。")
    op.create_unique_constraint("uq_messages_conversation_sequence", "messages", ["conversation_id", "sequence"])


def downgrade():
    op.drop_constraint("uq_messages_conversation_sequence", "messages", type_="unique")
