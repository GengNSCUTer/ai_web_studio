"""双重消息引用在事务末检查，允许ChatRun级联清理AgentRun后再检查引用。"""

from alembic import op

revision = "20261108_03"
down_revision = "20261108_02"
branch_labels = None
depends_on = None


def upgrade():
    # 同时保留会话/消息审计引用与ChatRun级联时，立即检查会依赖AFTER触发器执行顺序。
    # 仅推迟校验时间；提交时仍不允许任何悬空引用。
    op.execute("ALTER TABLE agent_runs ALTER CONSTRAINT agent_runs_assistant_message_id_fkey DEFERRABLE INITIALLY DEFERRED")
    op.execute("ALTER TABLE agent_runs ALTER CONSTRAINT agent_runs_conversation_id_fkey DEFERRABLE INITIALLY DEFERRED")


def downgrade():
    op.execute("ALTER TABLE agent_runs ALTER CONSTRAINT agent_runs_assistant_message_id_fkey NOT DEFERRABLE")
    op.execute("ALTER TABLE agent_runs ALTER CONSTRAINT agent_runs_conversation_id_fkey NOT DEFERRABLE")
