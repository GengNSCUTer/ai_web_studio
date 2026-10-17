"""对本地服务做受控 5/10/20 并发就绪探测。

探测只访问健康检查接口，不调用模型、不创建用户数据，也不把结果解释为
Chat 吞吐 SLA。它用于观察当前部署在不同瞬时并发下的完成率、首响应延迟、
总耗时和依赖就绪状态，正式容量仍需按目标机器和业务流量重新压测。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from typing import Any

import httpx


async def _one(client: httpx.AsyncClient, path: str) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        response = await client.get(path)
        elapsed_ms = (time.perf_counter() - started) * 1000
        payload: dict[str, Any] = {}
        try:
            decoded = response.json()
            if isinstance(decoded, dict):
                payload = decoded
        except ValueError:
            pass
        return {
            "ok": response.status_code == 200,
            "status_code": response.status_code,
            "first_response_ms": round(elapsed_ms, 2),
            "checks": payload.get("checks", {}),
        }
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "status_code": None,
            "first_response_ms": round((time.perf_counter() - started) * 1000, 2),
            "error": type(exc).__name__,
        }


async def probe(*, base_url: str, path: str, concurrency: int, timeout: float) -> dict[str, Any]:
    started = time.perf_counter()
    limits = httpx.Limits(max_connections=concurrency, max_keepalive_connections=concurrency)
    async with httpx.AsyncClient(
        base_url=base_url.rstrip("/"),
        timeout=timeout,
        trust_env=False,
        limits=limits,
    ) as client:
        results = await asyncio.gather(*(_one(client, path) for _ in range(concurrency)))
    latencies = sorted(float(item["first_response_ms"]) for item in results)
    successful = sum(1 for item in results if item["ok"])
    p95_index = max(0, min(len(latencies) - 1, int(len(latencies) * 0.95) - 1))
    dependency_states: dict[str, dict[str, int]] = {}
    for item in results:
        checks = item.get("checks") or {}
        if isinstance(checks, dict):
            for name, detail in checks.items():
                state = str(detail.get("status", "unknown")) if isinstance(detail, dict) else str(detail)
                dependency_states.setdefault(name, {})[state] = dependency_states.setdefault(name, {}).get(state, 0) + 1
    return {
        "concurrency": concurrency,
        "requests": len(results),
        "completed": successful,
        "failed": len(results) - successful,
        "completion_rate": round(successful / len(results), 4) if results else 0,
        "first_response_ms": {
            "min": round(min(latencies), 2) if latencies else None,
            "p50": round(statistics.median(latencies), 2) if latencies else None,
            "p95": round(latencies[p95_index], 2) if latencies else None,
            "max": round(max(latencies), 2) if latencies else None,
        },
        "total_elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "dependency_states": dependency_states,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:32007")
    parser.add_argument("--path", default="/health/ready")
    parser.add_argument("--timeout", type=float, default=10.0)
    parser.add_argument("--levels", default="5,10,20", help="逗号分隔的探测并发数")
    args = parser.parse_args()
    levels = [int(value.strip()) for value in args.levels.split(",") if value.strip()]
    if not levels or any(level < 1 or level > 20 for level in levels):
        parser.error("levels 必须是 1 到 20 的正整数")
    results = [
        asyncio.run(probe(base_url=args.base_url, path=args.path, concurrency=level, timeout=args.timeout))
        for level in levels
    ]
    print(json.dumps({"base_url": args.base_url, "path": args.path, "results": results}, ensure_ascii=False))
    return 0 if all(item["failed"] == 0 for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
