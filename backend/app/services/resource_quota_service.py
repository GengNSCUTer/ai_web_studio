"""在业务写入前检查用户容量，不在模型等待期间持有数据库连接。"""

from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
import asyncio

import anyio
from fastapi import HTTPException
from sqlalchemy import delete, func, select, text, update

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.resource_lease import ResourceLease
from app.models.knowledge import KnowledgeJob


def quota_lock(db, user_id):
    if db.bind.dialect.name == "postgresql":
        db.execute(text("select pg_advisory_xact_lock(hashtext(:key))"), {"key": f"aiws:quota:{user_id}"})


def exhausted(message):
    return HTTPException(status_code=429, detail=message, headers={"Retry-After": "5"})


class ResourceQuotaService:
    def __init__(self, session_factory=SessionLocal):
        self.sessions = session_factory

    def acquire(self, user_id, resource, limit):
        if limit < 1 or settings.resource_lease_seconds < 15:
            raise RuntimeError("用户限额必须为正数，租约时间至少 15 秒")
        now = datetime.now(timezone.utc)
        with self.sessions() as db:
            quota_lock(db, user_id)
            db.execute(delete(ResourceLease).where(ResourceLease.user_id == user_id, ResourceLease.expires_at <= now))
            count = db.scalar(select(func.count()).select_from(ResourceLease).where(
                ResourceLease.user_id == user_id, ResourceLease.resource == resource))
            if count >= limit:
                raise exhausted("当前账号并发任务已达上限，请等待已有任务结束")
            token = str(uuid4())
            db.add(ResourceLease(id=token, user_id=user_id, resource=resource, reserved_bytes=0,
                                 expires_at=now + timedelta(seconds=settings.resource_lease_seconds)))
            db.commit()
            return token

    def renew(self, token):
        now = datetime.now(timezone.utc)
        with self.sessions() as db:
            result = db.execute(update(ResourceLease).where(ResourceLease.id == token,
                ResourceLease.expires_at > now).values(expires_at=now + timedelta(seconds=settings.resource_lease_seconds)))
            db.commit()
            return result.rowcount == 1

    def release(self, token):
        with self.sessions() as db:
            lease = db.get(ResourceLease, token)
            if lease:
                # 与存储额度检查串行，避免文件刚写完、扫描尚未看到时释放预留额度。
                quota_lock(db, lease.user_id)
                db.delete(lease)
                db.commit()

    def reserve_upload(self, token, byte_count):
        with self.sessions() as db:
            lease = db.get(ResourceLease, token)
            if not lease:
                raise exhausted("上传容量租约已失效，请重新上传")
            quota_lock(db, lease.user_id)
            now = datetime.now(timezone.utc)
            # 此扫描在用户短锁内进行；其他上传释放预留额度前必须取得同一把锁。
            root = Path(settings.upload_dir) / lease.user_id
            used = sum(p.stat().st_size for p in root.rglob("*") if p.is_file() and not p.is_symlink()) if root.exists() else 0
            reserved = db.scalar(select(func.coalesce(func.sum(ResourceLease.reserved_bytes), 0)).where(
                ResourceLease.user_id == lease.user_id, ResourceLease.id != token, ResourceLease.expires_at > now))
            expires = lease.expires_at
            if expires.tzinfo is None:
                expires = expires.replace(tzinfo=timezone.utc)
            if expires <= now:
                raise exhausted("上传容量租约已失效，请重新上传")
            if byte_count < 0 or used + reserved + byte_count > settings.user_upload_storage_bytes:
                raise exhausted("当前账号上传存储额度不足，请先清理文件")
            lease.reserved_bytes = byte_count
            db.commit()


@asynccontextmanager
async def resource_capacity(user_id, resource, limit, *, service=None):
    service = service or ResourceQuotaService()
    token = None
    # 登记和清理都防止请求取消打断；否则已写入的租约只能等待过期。
    try:
        with anyio.CancelScope(shield=True):
            token = await anyio.to_thread.run_sync(service.acquire, user_id, resource, limit)
        request_task = asyncio.current_task()
        async def heartbeat():
            while True:
                await anyio.sleep(settings.resource_lease_seconds / 3)
                try:
                    renewed = await anyio.to_thread.run_sync(service.renew, token)
                except Exception:
                    renewed = False
                if not renewed:
                    # 额度已失去所有权时取消当前请求，不能静默无限占用资源。
                    request_task.cancel()
                    return
        heartbeat_task = asyncio.create_task(heartbeat())
        try:
            yield token
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
    finally:
        if token:
            with anyio.CancelScope(shield=True):
                await anyio.to_thread.run_sync(service.release, token)


def check_pending_knowledge_jobs(db, user_id, *, exclude_job_id=None):
    """调用方在同一个事务中创建 Job/Outbox，配额检查失败不会提交任何半成品。"""
    quota_lock(db, user_id)
    stmt = select(func.count()).select_from(KnowledgeJob).where(
        KnowledgeJob.user_id == user_id, KnowledgeJob.status.in_(("pending", "running")))
    if exclude_job_id:
        stmt = stmt.where(KnowledgeJob.id != exclude_job_id)
    with db.no_autoflush:
        count = db.scalar(stmt)
    if count >= settings.user_pending_knowledge_jobs:
        raise exhausted("当前账号待处理知识库任务已达上限，请稍后重试")
