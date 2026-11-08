"""独立Chat进程入口；沿用受控Provider/审批池，Web不拥有生成Task。"""

import asyncio
import signal

from app.core.database import SessionLocal, engine
from app.core.startup import ensure_runtime_schema
from app.services.approval_checkpoint_service import approval_pool_lifespan
from app.services.chat_run_worker import ChatRunWorker
from app.services.provider_client_pool import start_provider_client_pool, close_provider_client_pool


async def main():
    ensure_runtime_schema()
    worker = ChatRunWorker(session_factory=SessionLocal)
    for signum in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(signum, worker.request_stop)
    start_provider_client_pool()
    try:
        with approval_pool_lifespan(engine.url):
            await worker.run_forever()
    finally:
        await close_provider_client_pool()


if __name__ == "__main__":
    asyncio.run(main())
