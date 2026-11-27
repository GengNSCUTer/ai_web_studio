"""由 API 进程共享一个 PostgreSQL 连接唤醒 SSE 订阅。"""

import asyncio
from contextlib import asynccontextmanager, contextmanager, suppress
import logging

import psycopg
from sqlalchemy.engine import URL

logger = logging.getLogger(__name__)


class ChatEventListener:
    def __init__(self, database_url: URL):
        self._conninfo = database_url.set(drivername="postgresql").render_as_string(hide_password=False)
        self._subscribers: dict[str, set[asyncio.Event]] = {}
        self.ready = False

    @contextmanager
    def subscribe(self, run_id: str):
        wake = asyncio.Event()
        subscribers = self._subscribers.setdefault(run_id, set())
        subscribers.add(wake)
        try:
            yield wake
        finally:
            subscribers.remove(wake)
            if not subscribers:
                del self._subscribers[run_id]

    def _set_ready(self, ready: bool):
        if self.ready != ready:
            self.ready = ready
            # 断开时立即开始轮询。重新监听成功后补查，覆盖断线期间提交的事件。
            for subscribers in self._subscribers.values():
                for wake in subscribers:
                    wake.set()

    async def run(self):
        while True:
            try:
                async with await psycopg.AsyncConnection.connect(self._conninfo, autocommit=True,
                    application_name="aiws-api-chat-listener", connect_timeout=3) as db:
                    await asyncio.wait_for(db.execute("LISTEN aiws_chat_events"), timeout=3)
                    self._set_ready(True)
                    logger.info("Chat 事件监听已就绪")
                    while True:
                        async for notice in db.notifies(timeout=5):
                            for wake in self._subscribers.get(notice.payload, ()):
                                wake.set()
                        # 空闲连接也定期探活，及时发现 TCP 半开连接。
                        await asyncio.wait_for(db.execute("SELECT 1"), timeout=3)
            except (psycopg.Error, OSError, asyncio.TimeoutError) as exc:
                logger.warning("Chat 事件监听暂不可用，切换轮询 (%s)", type(exc).__name__)
            finally:
                self._set_ready(False)
            await asyncio.sleep(1)


@asynccontextmanager
async def chat_event_listener_lifespan(app, database_url: URL, *, enabled: bool):
    if not enabled:
        yield
        return
    listener = ChatEventListener(database_url)
    app.state.chat_event_listener = listener
    task = asyncio.create_task(listener.run())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        del app.state.chat_event_listener
