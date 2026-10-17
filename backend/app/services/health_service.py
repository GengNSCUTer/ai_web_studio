"""就绪探针只检查必要依赖，不执行模型请求或修改业务数据。"""

import asyncio
import time

import psycopg
from redis import Redis
from redis.backoff import NoBackoff
from redis.retry import Retry

from app.core.config import settings


def check_postgres() -> None:
    # 不借用业务连接池，避免探针排在长事务后无限等待。
    with psycopg.connect(
        host=settings.postgres_host,
        port=settings.postgres_port,
        dbname=settings.postgres_db,
        user=settings.postgres_user,
        password=settings.postgres_password,
        connect_timeout=2,
        options="-c statement_timeout=2000",
    ) as connection:
        connection.execute("SELECT 1").fetchone()


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
        await asyncio.wait_for(asyncio.to_thread(check), timeout=3)
        status = "ok"
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
