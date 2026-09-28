"""由进程管理器运行只读 Durable Tool Worker。"""

import asyncio
import os
import signal

from app.core.database import SessionLocal
from app.services.durable_tool_runtime import DurableToolWorker


async def main() -> None:
    worker = DurableToolWorker(session_factory=SessionLocal, owner=os.getenv("AGENT_WORKER_ID"))
    poll_interval = float(os.getenv("AGENT_WORKER_POLL_SECONDS", "1"))
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, worker.request_stop)
    await worker.run_forever(poll_interval_seconds=poll_interval)


if __name__ == "__main__":
    asyncio.run(main())
