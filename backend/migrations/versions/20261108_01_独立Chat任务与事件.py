"""独立Chat生成任务和可重放增量事件；DDL固定，不依赖后续ORM版本。"""

from alembic import op

revision = "20261108_01"
down_revision = "20261106_02"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
    CREATE TABLE chat_runs (
        id VARCHAR(36) NOT NULL,
        user_id VARCHAR(36) NOT NULL,
        conversation_id VARCHAR(36) NOT NULL,
        user_message_id VARCHAR(36) NOT NULL,
        assistant_message_id VARCHAR(36) NOT NULL,
        generation_id VARCHAR(36) NOT NULL,
        client_request_id VARCHAR(128) NOT NULL,
        status VARCHAR(32) NOT NULL,
        request_json TEXT NOT NULL,
        phase VARCHAR(32) NOT NULL,
        content TEXT NOT NULL,
        reasoning_content TEXT NOT NULL,
        sources_json TEXT NOT NULL,
        context_json TEXT NOT NULL,
        citation_audit_json TEXT,
        event_sequence INTEGER NOT NULL,
        events_pruned_through INTEGER NOT NULL,
        event_bytes INTEGER NOT NULL,
        model_attempts INTEGER NOT NULL,
        max_output_tokens INTEGER NOT NULL,
        lease_owner VARCHAR(128),
        lease_version INTEGER NOT NULL,
        lease_expires_at TIMESTAMP WITH TIME ZONE,
        heartbeat_at TIMESTAMP WITH TIME ZONE,
        queued_expires_at TIMESTAMP WITH TIME ZONE NOT NULL,
        deadline_at TIMESTAMP WITH TIME ZONE,
        cancel_requested_at TIMESTAMP WITH TIME ZONE,
        error_code VARCHAR(64),
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        finished_at TIMESTAMP WITH TIME ZONE,
        PRIMARY KEY (id),
        CONSTRAINT uq_chat_run_request UNIQUE (user_id, client_request_id),
        CONSTRAINT uq_chat_run_generation UNIQUE (assistant_message_id, generation_id),
        FOREIGN KEY(user_id) REFERENCES users (id),
        FOREIGN KEY(conversation_id) REFERENCES conversations (id) ON DELETE CASCADE,
        FOREIGN KEY(user_message_id) REFERENCES messages (id) ON DELETE CASCADE,
        FOREIGN KEY(assistant_message_id) REFERENCES messages (id) ON DELETE CASCADE
    )
    """)
    op.execute("""
    CREATE INDEX ix_chat_runs_claim ON chat_runs (status, created_at)
    """)
    op.execute("""
    CREATE INDEX ix_chat_runs_conversation ON chat_runs (conversation_id, created_at)
    """)
    op.execute("""
    CREATE INDEX ix_chat_runs_user_status ON chat_runs (user_id, status)
    """)
    op.execute("""
    CREATE TABLE chat_run_events (
        id VARCHAR(36) NOT NULL,
        run_id VARCHAR(36) NOT NULL,
        sequence INTEGER NOT NULL,
        event_json TEXT NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL,
        PRIMARY KEY (id),
        CONSTRAINT uq_chat_run_event_sequence UNIQUE (run_id, sequence),
        FOREIGN KEY(run_id) REFERENCES chat_runs (id) ON DELETE CASCADE
    )
    """)
    op.execute("""
    CREATE INDEX ix_chat_run_events_run_id ON chat_run_events (run_id)
    """)


def downgrade():
    op.drop_table("chat_run_events")
    op.drop_table("chat_runs")
