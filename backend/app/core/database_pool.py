"""集中定义每个进程的 PostgreSQL 连接池预算。"""

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class DatabasePoolBudget:
    role: str
    size: int
    overflow: int
    timeout: int
    async_size: int
    graph_size: int
    approval_size: int

    def sqlalchemy_options(self, *, asynchronous: bool = False) -> dict:
        return {
            "pool_pre_ping": True,
            "pool_size": self.async_size if asynchronous else self.size,
            "max_overflow": 0 if asynchronous else self.overflow,
            "pool_timeout": self.timeout,
            "connect_args": {"application_name": f"aiws-{self.role}-{'async' if asynchronous else 'sql'}"},
        }


# 同步常驻额、同步溢出额、图检查点、文件审批检查点。
ROLE_DEFAULTS = {
    "api": (5, 3, 2, 2),
    "chat-worker": (5, 3, 4, 2),
    "durable-worker": (3, 1, 2, 1),
    "knowledge-worker": (3, 1, 1, 1),
    "memory-worker": (2, 1, 1, 1),
    "summary-worker": (2, 1, 1, 1),
    "sandbox-worker": (2, 0, 1, 1),
    "cli": (2, 1, 1, 1),
}


def resolve_database_pool_budget(role: str, env: Mapping[str, str]) -> DatabasePoolBudget:
    if role not in ROLE_DEFAULTS:
        raise ValueError(f"未知数据库池角色：{role}")
    role_prefix = "POSTGRES_" + role.upper().replace("-", "_") + "_"

    def read(key: str, default: int, *, minimum: int = 1) -> int:
        for name in (role_prefix + key, "POSTGRES_" + key):
            if name not in env:
                continue
            try:
                value = int(env[name])
                if value >= minimum:
                    return value
            except ValueError:
                pass
            raise ValueError(f"{name} 必须是大于等于 {minimum} 的整数")
        return default

    size, overflow, graph, approval = ROLE_DEFAULTS[role]
    return DatabasePoolBudget(
        role=role,
        size=read("POOL_SIZE", size),
        overflow=read("MAX_OVERFLOW", overflow, minimum=0),
        timeout=read("POOL_TIMEOUT_SECONDS", 10),
        async_size=read("ASYNC_POOL_SIZE", 2),
        graph_size=read("GRAPH_POOL_SIZE", graph),
        approval_size=read("APPROVAL_POOL_SIZE", approval),
    )
