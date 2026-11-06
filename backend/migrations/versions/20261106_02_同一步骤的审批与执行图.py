"""同一文档交付Step分别保存审批图与Worker执行图，仍按运行时保持唯一映射。"""

from alembic import op

revision = "20261106_02"
down_revision = "20261106_01"
branch_labels = None
depends_on = None


def upgrade():
    # thread主键与Run/Step外键继续保留；只有“每Step仅一个图”的限制调整。
    op.execute("ALTER TABLE aiws_graph.runtime_threads ADD COLUMN runtime_key TEXT NOT NULL DEFAULT 'legacy'")
    op.execute("UPDATE aiws_graph.runtime_threads SET runtime_key=split_part(thread_id,':',1)")
    op.execute("ALTER TABLE aiws_graph.runtime_threads DROP CONSTRAINT runtime_threads_step_id_key")
    op.execute("ALTER TABLE aiws_graph.runtime_threads ADD CONSTRAINT uq_graph_step_runtime UNIQUE(step_id,runtime_key)")


def downgrade():
    # 有交付Step已存两种图时不能自动丢弃任一记录来凑旧约束。
    op.execute("ALTER TABLE aiws_graph.runtime_threads ADD CONSTRAINT runtime_threads_step_id_key UNIQUE(step_id)")
    op.execute("ALTER TABLE aiws_graph.runtime_threads DROP CONSTRAINT uq_graph_step_runtime")
    op.execute("ALTER TABLE aiws_graph.runtime_threads DROP COLUMN runtime_key")
