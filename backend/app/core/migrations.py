"""显式管理数据库版本；首次收编必须校验历史结构，不能盲目 stamp。"""

from pathlib import Path
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

CONFIG_PATH = Path(__file__).resolve().parents[2] / "alembic.ini"
FIRST_REVISION = "20261017_01"


def migration_config():
    return Config(str(CONFIG_PATH))


def validate_legacy_schema(connection):
    revision = ScriptDirectory.from_config(migration_config()).get_revision(FIRST_REVISION)
    expected = revision.module.legacy_schema
    inspector = inspect(connection)
    for table, spec in expected.items():
        if not inspector.has_table(table):
            raise RuntimeError(f"历史结构缺少表 {table}，拒绝收编")
        columns = {item["name"]: item for item in inspector.get_columns(table)}
        for name, (sql_type, nullable) in spec["columns"].items():
            actual = columns.get(name)
            if actual is None or str(actual["type"]) != sql_type or actual["nullable"] != nullable:
                raise RuntimeError(f"历史结构列不匹配：{table}.{name}，需先显式处理旧版本")
        indexes = {item["name"] for item in inspector.get_indexes(table)}
        if set(spec["indexes"]) - indexes:
            raise RuntimeError(f"历史结构缺少已约定索引：{table}")
        foreign_keys = [item["constrained_columns"] for item in inspector.get_foreign_keys(table)]
        if any(key not in foreign_keys for key in spec["foreign_keys"]):
            raise RuntimeError(f"历史结构缺少关联约束：{table}")


def upgrade_database(bind, *, adopt_legacy=False, target="head"):
    """所有版本变更在一个 PostgreSQL 事务中执行，失败时版本号和 DDL 一起回滚。"""
    with bind.begin() as connection:
        connection.execute(text("select pg_advisory_xact_lock(hashtext('aiws:schema:migrate'))"))
        config = migration_config()
        config.attributes["connection"] = connection
        current = MigrationContext.configure(connection).get_current_revision()
        tables = set(inspect(connection).get_table_names()) - {"alembic_version"}
        if current is None and tables:
            if not adopt_legacy:
                raise RuntimeError("数据库已有数据但没有版本；请先备份，再使用 --adopt-legacy")
            validate_legacy_schema(connection)
            command.stamp(config, FIRST_REVISION)
        command.upgrade(config, target)


def require_current_schema(bind):
    with bind.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()
    head = ScriptDirectory.from_config(migration_config()).get_current_head()
    if current != head:
        raise RuntimeError("数据库版本未就绪；请先备份并执行 python -m scripts.migrate_database upgrade")
