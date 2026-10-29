"""分开检查 Web 依赖与后台 Worker，不执行工具或修改业务数据。"""

import asyncio
import time

import psycopg
from redis import Redis
from redis.backoff import NoBackoff
from redis.retry import Retry

from app.core.config import settings
from app.services.durable_worker_registry import DurableWorkerRegistry


def _connect_postgres() -> psycopg.Connection:
    # 不借用业务连接池，避免探针排在长事务后无限等待。
    return psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
        connect_timeout=2,
        options="-c statement_timeout=2000",
    )


def check_postgres() -> None:
    with _connect_postgres() as connection:
        connection.execute("SELECT 1").fetchone()


def check_durable_worker() -> bool:
    """即使没有待执行任务，也要求至少一个 Worker 有有效心跳。"""
    with _connect_postgres() as connection:
        row = connection.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM agent_worker_heartbeats
                WHERE status IN ('idle', 'running')
                  AND heartbeat_at >= CURRENT_TIMESTAMP - (%s * INTERVAL '1 second')
            )
            """,
            (DurableWorkerRegistry.STALE_SECONDS,),
        ).fetchone()
        return bool(row and row[0])


def check_redis() -> None:
    with Redis.from_url(
        settings.knowledge_redis_url,
        socket_connect_timeout=2,
        socket_timeout=2,
        retry_on_timeout=False,
        # 探针失败立即返回，不沿用客户端默认的多次退避重试。
        retry=Retry(NoBackoff(), 0),
    ) as client:
        client.ping()


async def _probe(check) -> dict[str, object]:
    started = time.monotonic()
    try:
        result = await asyncio.wait_for(asyncio.to_thread(check), timeout=3)
        status = "unavailable" if result is False else "ok"
    except Exception:
        # 不输出异常原文；其中可能包含服务器地址或带密码的连接 URL。
        status = "unavailable"
    return {"status": status, "latency_ms": round((time.monotonic() - started) * 1000, 1)}


async def readiness() -> dict[str, object]:
    postgres, redis = await asyncio.gather(_probe(check_postgres), _probe(check_redis))
    checks = {"postgres": postgres, "redis": redis}
    return {
        "status": "ready" if all(item["status"] == "ok" for item in checks.values()) else "not_ready",
        "checks": checks,
    }


async def worker_readiness() -> dict[str, object]:
    """后台能力单独返回状态，避免 Worker 故障使普通 Chat 被摘流。"""
    durable = await _probe(check_durable_worker)
    return {
        "status": "ready" if durable["status"] == "ok" else "not_ready",
        "checks": {"durable_worker": durable},
    }
