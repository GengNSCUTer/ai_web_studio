import asyncio

from app.services.memory_candidate_runtime import MemoryCandidateWorker


async def main() -> None:
    worker = MemoryCandidateWorker()
    while True:
        worked = await worker.run_once()
        if not worked:
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(main())
