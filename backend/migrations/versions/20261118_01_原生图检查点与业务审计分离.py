"""清理旧恢复表，保留独立于图执行的业务审计。"""

from alembic import op

revision = "20261118_01"
down_revision = "20261117_01"
branch_labels = None
depends_on = None


def upgrade():
    # 维护期间须停写。锁定后检查任务，避免检查与删除之间产生新旧混跑。
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("LOCK TABLE public.chat_runs IN SHARE MODE")
    op.execute("""DO $$ BEGIN
        IF EXISTS (SELECT 1 FROM public.chat_runs WHERE status IN ('queued','running','cancel_requested')) THEN
            RAISE EXCEPTION 'Chat尚未排空，不能删除旧恢复数据';
        END IF;
    END $$""")
    op.execute("DELETE FROM aiws_graph.runtime_threads WHERE runtime_key = 'langgraph_chat_tools_v1'")
    op.execute("""DELETE FROM public.agent_checkpoints c USING public.agent_runs r
        WHERE c.run_id = r.id AND r.runtime_kind = 'chat_tool_workflow'""")
    op.execute("""UPDATE public.agent_runs SET planner_state_json = '{}'
        WHERE runtime_kind = 'chat_tool_workflow' AND planner_state_json <> '{}'""")
    op.rename_table("agent_checkpoints", "agent_run_audit_events")
    op.execute("ALTER INDEX ix_agent_checkpoints_run_id RENAME TO ix_agent_run_audit_events_run_id")
    for old, new in (("agent_checkpoints_run_id_fkey", "agent_run_audit_events_run_id_fkey"),
                     ("agent_checkpoints_pkey", "agent_run_audit_events_pkey"),
                     ("uq_agent_checkpoint_version", "uq_agent_run_audit_event_version")):
        op.execute(f"ALTER TABLE agent_run_audit_events RENAME CONSTRAINT {old} TO {new}")
    op.alter_column("agent_run_audit_events", "observations_json", new_column_name="details_json")
    op.drop_column("agent_run_audit_events", "planner_state_json")
    op.drop_column("agent_run_audit_events", "remaining_budget_json")


def downgrade():
    # 已退役的图和快照不能由审计反向生成，回退应恢复升级前的完整备份。
    raise RuntimeError("此迁移删除了旧执行快照；请使用升级前的备份回退")
