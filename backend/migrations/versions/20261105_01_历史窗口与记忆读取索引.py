"""为历史窗口、提取游标和记忆有效版本清单建立联合索引。"""

from alembic import op

revision = "20261105_01"
down_revision = "20261103_04"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_messages_context_order", "messages", ["conversation_id", "created_at", "sequence", "id"])
    op.create_index("ix_messages_extraction_cursor", "messages", ["conversation_id", "role", "sequence"])
    op.create_index("ix_user_memories_active_lookup", "user_memories", ["user_id", "status", "is_enabled", "id"])


def downgrade():
    op.drop_index("ix_user_memories_active_lookup", table_name="user_memories")
    op.drop_index("ix_messages_extraction_cursor", table_name="messages")
    op.drop_index("ix_messages_context_order", table_name="messages")
