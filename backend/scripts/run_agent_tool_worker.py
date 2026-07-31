"""Run the durable read-only Tool worker under a supervisor in production."""

import asyncio
import os

from app.core.database import SessionLocal
from app.services.durable_tool_runtime import DurableToolWorker


async def main() -> None:
    worker = DurableToolWorker(session_factory=SessionLocal, owner=os.getenv("AGENT_WORKER_ID"))
    poll_interval = float(os.getenv("AGENT_WORKER_POLL_SECONDS", "1"))
    await worker.run_forever(poll_interval_seconds=poll_interval)


if __name__ == "__main__":
    asyncio.run(main())
