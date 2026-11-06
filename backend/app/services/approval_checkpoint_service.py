"""文件审批图的原生检查点；只在一次预览/批准请求期间持有连接。"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import threading

from langgraph.checkpoint.postgres import PostgresSaver
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from sqlalchemy.engine import make_url

from app.services.durable_checkpoint_service import checkpoint_conninfo


APPROVAL_GRAPH_VERSION = "langgraph_approval_v1"
_pools: dict[str, ConnectionPool] = {}
_pool_lock = threading.Lock()


class ApprovalCheckpointConflict(RuntimeError):
    """审批图范围或业务版本已经失效。"""


@dataclass(frozen=True)
class ApprovalIdentity:
    user_id: str
    project_id: str
    run_id: str
    step_id: str
    approval_id: str
    state_version: int

    @property
    def thread_id(self):
        return f"{APPROVAL_GRAPH_VERSION}:u:{self.user_id}:p:{self.project_id}:r:{self.run_id}:s:{self.step_id}"

    @property
    def config(self):
        return {"configurable": {"thread_id": self.thread_id, "checkpoint_ns": ""}, "recursion_limit": 16}


def _new_pool(url):
    return ConnectionPool(checkpoint_conninfo(url), open=True, min_size=0, max_size=4, timeout=10,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row,
                "connect_timeout": 5,
                "options": "-c search_path=aiws_graph,public -c lock_timeout=5000 -c statement_timeout=15000"})


@contextmanager
def approval_pool_lifespan(url):
    """同步审批入口复用有界池，不在等待用户批准期间持有连接或事务。"""
    if make_url(url).get_backend_name() != "postgresql":
        yield
        return
    key = checkpoint_conninfo(url)
    with _pool_lock:
        own = key not in _pools
        if own:
            _pools[key] = _new_pool(url)
    try:
        yield
    finally:
        if own:
            with _pool_lock:
                pool = _pools.pop(key)
            pool.close()


class ApprovalPostgresSaver(PostgresSaver):
    """检查点只是执行记录，写入前仍在同一事务复核业务范围和版本。"""

    def __init__(self, conn, identity):
        super().__init__(conn)
        self.identity = identity
        self.fence_lock = threading.Lock()

    def _scope(self, config):
        values = config.get("configurable", {})
        if values.get("thread_id") != self.identity.thread_id or values.get("checkpoint_ns", ""):
            raise ApprovalCheckpointConflict("审批图身份或命名空间不匹配")

    def get_tuple(self, config):
        self._scope(config)
        return super().get_tuple(config)

    def _verify(self):
        item = self.identity
        row = self.conn.execute("""SELECT r.state_version,r.status AS run_status,a.status AS approval_status
            FROM public.agent_runs r JOIN public.agent_steps s ON s.run_id=r.id
            JOIN public.agent_approvals a ON a.run_id=r.id AND a.step_id=s.id
            WHERE r.id=%s AND s.id=%s AND a.id=%s AND r.user_id=%s AND a.user_id=%s
              AND r.project_id=%s AND r.runtime_kind='file_edit'
              AND a.action='workspace.files.apply_edit' FOR UPDATE OF r""",
            (item.run_id, item.step_id, item.approval_id, item.user_id, item.user_id, item.project_id)).fetchone()
        if not row:
            raise ApprovalCheckpointConflict("审批范围已失效")
        pending = (row["state_version"] == item.state_version
                   and row["run_status"] == "waiting_approval" and row["approval_status"] in {"pending", "expired"})
        terminal = (row["state_version"] == item.state_version + 1
                    and row["run_status"] in {"succeeded", "cancelled", "conflict"}
                    and row["approval_status"] in {"consumed", "rejected", "expired"})
        if not (pending or terminal):
            raise ApprovalCheckpointConflict("审批图业务版本已变化")

    def put(self, config, checkpoint, metadata, new_versions):
        self._scope(config)
        with self.fence_lock:
            with self.conn.transaction():
                self._verify()
                return super().put(config, checkpoint, metadata, new_versions)

    def put_writes(self, config, writes, task_id, task_path=""):
        self._scope(config)
        safe = [(key, {"error_type": type(value).__name__} if key == "__error__" else value)
                for key, value in writes]
        with self.fence_lock:
            with self.conn.transaction():
                self._verify()
                return super().put_writes(config, safe, task_id, task_path)


@contextmanager
def approval_checkpointer(url, identity, saver_type=ApprovalPostgresSaver):
    """串行化同一审批的一次图调用；interrupt返回后立即释放全部资源。"""
    key = checkpoint_conninfo(url)
    with _pool_lock:
        pool = _pools.get(key)
    own = pool is None
    if own:
        pool = _new_pool(url)
    try:
        with pool.connection() as conn:
            conn.execute("SELECT pg_advisory_lock(hashtextextended(%s,0))", (identity.thread_id,))
            try:
                conn.execute("""INSERT INTO aiws_graph.runtime_threads(thread_id,run_id,step_id,runtime_key)
                    SELECT %s,r.id,s.id,%s FROM public.agent_runs r JOIN public.agent_steps s ON s.run_id=r.id
                    WHERE r.id=%s AND s.id=%s AND r.user_id=%s AND r.project_id=%s
                    ON CONFLICT(thread_id) DO NOTHING""",
                    (identity.thread_id, identity.thread_id.split(":", 1)[0], identity.run_id, identity.step_id, identity.user_id, identity.project_id))
                yield saver_type(conn, identity)
            finally:
                conn.execute("SELECT pg_advisory_unlock(hashtextextended(%s,0))", (identity.thread_id,))
    finally:
        if own:
            pool.close()
