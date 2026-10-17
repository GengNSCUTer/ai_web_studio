"""仅由显式迁移命令加载；应用启动不执行结构变更。"""

from alembic import context
from sqlalchemy import text

from app.core.database import Base, engine
from app import models  # noqa: F401


def run(connection):
    def include_object(obj, name, kind, reflected, compare_to):
        # 保留已存在的历史业务列；收编版本不能自动删除旧凭据或用户数据。
        return not (kind == "column" and reflected and compare_to is None)

    context.configure(connection=connection, target_metadata=context.config.attributes.get("target_metadata", Base.metadata),
                      compare_type=True, include_object=include_object)
    with context.begin_transaction():
        # 同一个数据库的升级、收编和版本戳必须串行，并与 DDL 一起提交。
        connection.execute(text("select pg_advisory_xact_lock(hashtext('aiws:schema:migrate'))"))
        context.run_migrations()


provided = context.config.attributes.get("connection")
if provided is not None:
    run(provided)
else:
    with engine.connect() as connection:
        run(connection)
