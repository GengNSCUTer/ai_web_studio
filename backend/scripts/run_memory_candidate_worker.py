import asyncio
import logging

from app.services.memory_candidate_runtime import MemoryCandidateWorker


async def main() -> None:
    worker = MemoryCandidateWorker()
    while True:
        worked = await worker.run_once()
        if not worked:
            await asyncio.sleep(1)


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    for name in ("memory_candidate_runtime", "memory_conflict_service", "memory_index_service"):
        logging.getLogger(f"app.services.{name}").setLevel(logging.INFO)
    asyncio.run(main())
