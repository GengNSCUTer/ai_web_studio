"""在独立schema显式建立LangGraph检查点表，业务表和版本仍由Alembic管理。"""

from alembic import op
import sqlalchemy as sa

from langgraph.checkpoint.postgres.base import MIGRATIONS

revision = "20261106_01"
down_revision = "20261105_02"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    bind.execute(sa.text("CREATE SCHEMA aiws_graph"))
    bind.execute(sa.text("SET LOCAL search_path TO aiws_graph, public"))
    # 本次只创建空的新表。移除CONCURRENTLY以保留既有DDL/版本原子迁移事务，
    # 不在Web启动时调用Saver.setup，也不在事务中创建并发索引。
    for version, statement in enumerate(MIGRATIONS):
        bind.execute(sa.text(statement.replace("INDEX CONCURRENTLY", "INDEX")))
        bind.execute(sa.text("INSERT INTO checkpoint_migrations(v) VALUES (:version)"), {"version": version})
    # 删除业务Run/Step时必须一并删除图数据，不能在框架原生表留下孤立线程。
    bind.execute(sa.text("""CREATE TABLE runtime_threads (
        thread_id TEXT PRIMARY KEY,
        run_id VARCHAR(36) NOT NULL REFERENCES public.agent_runs(id) ON DELETE CASCADE,
        step_id VARCHAR(36) NOT NULL REFERENCES public.agent_steps(id) ON DELETE CASCADE,
        UNIQUE(step_id)
    )"""))
    for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
        bind.execute(sa.text(f"ALTER TABLE {table} ADD CONSTRAINT fk_{table}_runtime_thread "
                             "FOREIGN KEY(thread_id) REFERENCES runtime_threads(thread_id) ON DELETE CASCADE"))
    bind.execute(sa.text("SET LOCAL search_path TO public"))


def downgrade():
    op.execute("DROP SCHEMA aiws_graph CASCADE")
