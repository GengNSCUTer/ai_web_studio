"""独立摘要修复Worker，停止后保留已提交批次。"""

import asyncio
import signal

from app.core.database import SessionLocal
from app.core.startup import ensure_runtime_schema
from app.services.provider_client_pool import start_provider_client_pool, close_provider_client_pool
from app.services.summary_rebuild_worker import SummaryRebuildWorker


async def main():
    ensure_runtime_schema()
    worker = SummaryRebuildWorker(session_factory=SessionLocal)
    for signum in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(signum, worker.request_stop)
    start_provider_client_pool()
    try:
        await worker.run_forever()
    finally:
        await close_provider_client_pool()


if __name__ == "__main__":
    asyncio.run(main())
