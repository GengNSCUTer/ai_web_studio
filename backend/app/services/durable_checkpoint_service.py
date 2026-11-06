"""PG图检查点：独立连接池、明确命名范围，并在同一事务中检查租约后写入。"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from weakref import WeakKeyDictionary

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.postgres.base import MIGRATIONS
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool
from sqlalchemy.engine import make_url


GRAPH_RUNTIME_VERSION = "langgraph_tool_v1"
_pools: WeakKeyDictionary = WeakKeyDictionary()


class CheckpointLeaseLost(RuntimeError):
    """只读任务已被接管或取消，旧图不允许保存进度。"""


@dataclass(frozen=True)
class CheckpointIdentity:
    user_id: str
    project_id: str | None
    run_id: str
    step_id: str
    event_id: str
    owner: str
    lease_version: int

    @property
    def thread_id(self):
        # step独立恢复，不让同一Run的并行分支争写同一检查点。
        return f"{GRAPH_RUNTIME_VERSION}:u:{self.user_id}:p:{self.project_id or '-'}:r:{self.run_id}:s:{self.step_id}"

    @property
    def config(self):
        # checkpoint_ns为空表示本图根空间，版本已体现在thread_id；不冒充框架内部子图命名空间。
        return {"configurable": {"thread_id": self.thread_id, "checkpoint_ns": ""}, "recursion_limit": 16}


def checkpoint_conninfo(url) -> str:
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


async def _new_pool(conninfo):
    pool = AsyncConnectionPool(conninfo, open=False, min_size=0, max_size=4, timeout=10,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row,
                "options": "-c search_path=aiws_graph,public"})
    await pool.open()
    try:
        async with pool.connection() as conn:
            row = await (await conn.execute("SELECT max(v) AS version FROM aiws_graph.checkpoint_migrations")).fetchone()
            if row["version"] != len(MIGRATIONS) - 1:
                raise RuntimeError("图检查点版本未就绪，请先执行显式数据库迁移")
    except BaseException:
        await pool.close()
        raise
    return pool


@asynccontextmanager
async def checkpoint_pool_lifespan(url):
    """长期Worker/Web生命周期复用连接池；不跨事件循环共享连接。"""
    parsed = make_url(url)
    if parsed.get_backend_name() != "postgresql":
        yield
        return
    loop = asyncio.get_running_loop()
    key = checkpoint_conninfo(url)
    pools = _pools.setdefault(loop, {})
    if key in pools:
        yield
        return
    pool = await _new_pool(key)
    pools[key] = pool
    try:
        yield
    finally:
        pools.pop(key, None)
        await pool.close()


class LeaseFencedPostgresSaver(AsyncPostgresSaver):
    """在框架原生检查点写事务中锁定业务事件，防止先检查后被接管的窗口。"""

    def __init__(self, conn, identity: CheckpointIdentity):
        super().__init__(conn)
        self.identity = identity
        self.fence_lock = asyncio.Lock()

    def _scope(self, config):
        values = config.get("configurable", {})
        if values.get("thread_id") != self.identity.thread_id or values.get("checkpoint_ns", ""):
            raise CheckpointLeaseLost("检查点身份或版本空间不匹配")

    async def aget_tuple(self, config):
        self._scope(config)
        return await super().aget_tuple(config)

    async def _verify_lease(self, conn):
        identity = self.identity
        result = await conn.execute("""
            SELECT e.lease_version AS event_version, e.lease_owner AS event_owner,
                   e.status AS event_status, e.lease_expires_at > now() AS lease_live,
                   s.lease_version AS step_version, s.lease_owner AS step_owner, s.status AS step_status,
                   r.status AS run_status
            FROM public.agent_outbox_events e
            JOIN public.agent_steps s ON s.id=e.step_id AND s.run_id=e.run_id
            JOIN public.agent_runs r ON r.id=e.run_id
            WHERE e.id=%s AND s.id=%s AND r.id=%s AND r.user_id=%s
              AND r.project_id IS NOT DISTINCT FROM %s
            FOR UPDATE OF e
        """, (identity.event_id, identity.step_id, identity.run_id, identity.user_id, identity.project_id))
        row = await result.fetchone()
        if not row or row["event_version"] != identity.lease_version or row["step_version"] != identity.lease_version:
            raise CheckpointLeaseLost("图检查点租约版本已失效")
        running = (row["event_status"] == row["step_status"] == "running"
                   and row["event_owner"] == row["step_owner"] == identity.owner
                   and row["lease_live"] and row["run_status"] in {"queued", "running"})
        # 成功/重排/失败的业务事务会释放owner。该版本尚未被重新领取时，
        # 原执行者可补齐仅含ID的图记录；新版本一旦领取，此分支也必须拒绝。
        completed = (row["run_status"] in {"queued", "running", "succeeded", "failed", "dead_letter"}
            and row["event_owner"] is None and row["step_owner"] is None and (
            row["event_status"] == row["step_status"] in {"pending", "succeeded", "failed", "dead_letter"}
            or (row["event_status"] == "succeeded" and row["step_status"] == "skipped")))
        if not (running or completed):
            raise CheckpointLeaseLost("图检查点执行权已失效")

    async def aput(self, config, checkpoint, metadata, new_versions):
        self._scope(config)
        async with self.fence_lock:
            async with self.conn.transaction():
                await self._verify_lease(self.conn)
                return await super().aput(config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id, task_path=""):
        self._scope(config)
        # 框架可能保存异常对象；只留类型，不能把第三方URL/凭据写入checkpoint_writes。
        safe_writes = [(channel, {"error_type": type(value).__name__} if channel == "__error__" else value)
                       for channel, value in writes]
        async with self.fence_lock:
            async with self.conn.transaction():
                await self._verify_lease(self.conn)
                return await super().aput_writes(config, safe_writes, task_id, task_path)


@asynccontextmanager
async def durable_checkpointer(url, identity):
    """进程托管时取池连接；单次脚本使用短生命周期池，结束即释放。"""
    conninfo = checkpoint_conninfo(url)
    pool = _pools.get(asyncio.get_running_loop(), {}).get(conninfo)
    own_pool = pool is None
    if own_pool:
        pool = await _new_pool(conninfo)
    try:
        async with pool.connection() as conn:
            # 范围来自当前已认证的Run和服务端领取的Step，不接受用户给定thread_id。
            await conn.execute("""INSERT INTO aiws_graph.runtime_threads(thread_id,run_id,step_id)
                SELECT %s,r.id,s.id FROM public.agent_runs r JOIN public.agent_steps s ON s.run_id=r.id
                WHERE r.id=%s AND s.id=%s AND r.user_id=%s AND r.project_id IS NOT DISTINCT FROM %s
                ON CONFLICT(thread_id) DO NOTHING""",
                (identity.thread_id, identity.run_id, identity.step_id, identity.user_id, identity.project_id))
            yield LeaseFencedPostgresSaver(conn, identity)
    finally:
        if own_pool:
            await pool.close()
