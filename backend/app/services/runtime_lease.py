"""Worker共用的租约判断和异步心跳；不替代数据库锁及业务事务。"""

import asyncio
from datetime import datetime, timezone


def utcnow():
    return datetime.now(timezone.utc)


def aware(value):
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def owns_live_lease(row, *, owner, version, now=None):
    return bool(row and row.lease_owner == owner and row.lease_version == version
                and row.lease_expires_at and aware(row.lease_expires_at) > (now or utcnow()))


async def watch_lease(stop, *, interval, renew, lost):
    """独立短Session续租失败后通知执行者停止，绝不静默继续提交。"""
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            try:
                valid = await asyncio.to_thread(renew)
            except Exception:
                valid = False
            if not valid:
                lost()
                return
