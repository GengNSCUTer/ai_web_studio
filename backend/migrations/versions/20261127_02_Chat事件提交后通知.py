"""Chat 事件序号变化或任务删除时唤醒订阅者。"""

from alembic import op

revision = "20261127_02"
down_revision = "20261127_01"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE FUNCTION aiws_notify_chat_events() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                PERFORM pg_notify('aiws_chat_events', OLD.id);
            ELSE
                PERFORM pg_notify('aiws_chat_events', NEW.id);
            END IF;
            RETURN NULL;
        END;
        $$
    """)
    # 序号和事件同事务写入。租约心跳与事件裁剪不改变序号，无需通知。
    op.execute("""
        CREATE TRIGGER chat_run_events_wake_update
        AFTER UPDATE OF event_sequence ON chat_runs
        FOR EACH ROW WHEN (OLD.event_sequence IS DISTINCT FROM NEW.event_sequence)
        EXECUTE FUNCTION aiws_notify_chat_events()
    """)
    op.execute("""
        CREATE TRIGGER chat_run_events_wake_delete
        AFTER DELETE ON chat_runs
        FOR EACH ROW EXECUTE FUNCTION aiws_notify_chat_events()
    """)


def downgrade():
    op.execute("DROP TRIGGER chat_run_events_wake_delete ON chat_runs")
    op.execute("DROP TRIGGER chat_run_events_wake_update ON chat_runs")
    op.execute("DROP FUNCTION aiws_notify_chat_events()")
