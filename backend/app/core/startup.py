"""启动只验证版本，结构升级由部署前的显式迁移命令负责。"""

from app.core.database import engine
from app.core.migrations import require_current_schema


def ensure_runtime_schema() -> None:
    require_current_schema(engine)
