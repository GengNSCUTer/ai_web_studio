"""新增插件控制面与独立沙箱任务，不更改已有 Tool 权限。"""

from alembic import op
import sqlalchemy as sa

revision = "20261028_01"
down_revision = "20261017_02"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("plugin_installations",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("plugin_key", sa.String(128), nullable=False),
        sa.Column("release_id", sa.String(36), nullable=False),
        sa.Column("installed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "plugin_key", name="uq_plugin_user_key"))
    op.create_index("ix_plugin_installations_user_id", "plugin_installations", ["user_id"])
    op.create_table("plugin_releases",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("plugin_key", sa.String(128), nullable=False),
        sa.Column("version", sa.String(32), nullable=False),
        sa.Column("manifest_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_plugin_releases_user_id", "plugin_releases", ["user_id"])
    op.create_table("project_plugins",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("plugin_key", sa.String(128), nullable=False),
        sa.Column("is_enabled", sa.Boolean(), nullable=False),
        sa.UniqueConstraint("project_id", "plugin_key", name="uq_project_plugin"))
    for col in ("user_id", "project_id"):
        op.create_index(f"ix_project_plugins_{col}", "project_plugins", [col])
    op.create_table("plugin_submissions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_plugin_submissions_user_id", "plugin_submissions", ["user_id"])
    op.create_table("sandbox_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("project_id", sa.String(36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("plugin_key", sa.String(128), nullable=False),
        sa.Column("release_id", sa.String(36), nullable=False),
        sa.Column("script_key", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("plan_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("error", sa.String(256), nullable=True),
        sa.Column("lease_owner", sa.String(128), nullable=True),
        sa.Column("lease_version", sa.Integer(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    for col in ("user_id", "project_id", "status"):
        op.create_index(f"ix_sandbox_jobs_{col}", "sandbox_jobs", [col])
    op.create_table("sandbox_worker_states",
        sa.Column("owner", sa.String(128), primary_key=True),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("detail", sa.String(256), nullable=False),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=False))


def downgrade():
    for table in ("sandbox_worker_states", "sandbox_jobs", "plugin_submissions", "project_plugins", "plugin_releases", "plugin_installations"):
        op.drop_table(table)
