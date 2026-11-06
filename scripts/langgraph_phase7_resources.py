"""在真实业务批量运行期间只读采样服务资源，不制造额外模型请求或更改进程。"""

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.core.database import engine  # noqa: E402
from sqlalchemy import text  # noqa: E402


def sample():
    rows = {}
    for name in ("backend", "durable-worker", "knowledge-worker", "memory-worker"):
        raw = subprocess.check_output(["bash", "deploy/run_services.sh", "ctl", "pid", name], cwd=ROOT, text=True)
        pid = int(raw.strip())
        proc = Path("/proc") / str(pid)
        status = dict(line.split(":", 1) for line in (proc / "status").read_text().splitlines() if ":" in line)
        rows[name] = {"pid": pid, "rss_kib": int(status["VmRSS"].split()[0]),
            "threads": int(status["Threads"]), "fds": len(list((proc / "fd").iterdir()))}
    with engine.connect() as connection:
        sessions = {state or "unknown": count for state, count in connection.execute(text(
            "SELECT state,count(*) FROM pg_stat_activity WHERE datname=current_database() GROUP BY state"))}
        longest_idle = connection.scalar(text("SELECT coalesce(max(extract(epoch FROM now()-xact_start)),0) "
            "FROM pg_stat_activity WHERE datname=current_database() AND state='idle in transaction'"))
    with httpx.Client(timeout=5, trust_env=False) as client:
        health = {path: client.get("http://127.0.0.1:32007" + path).status_code
                  for path in ("/health/ready", "/health/workers")}
    return {"offset_seconds": round(time.monotonic()), "processes": rows, "pg_sessions": sessions,
        "max_idle_transaction_seconds": round(float(longest_idle), 3), "health": health}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--interval", type=float, default=10)
    args = parser.parse_args()
    if args.output.exists() or args.samples < 1 or args.interval <= 0:
        parser.error("使用新报告路径、正采样次数和间隔")
    report = {"scope": "业务验收期间的资源采样，不是生产容量或无泄漏证明", "status": "running",
              "planned_samples": args.samples, "samples": [], "sampling_failures": []}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    try:
        for index in range(args.samples):
            try:
                item = sample()
                item["offset_seconds"] = round(time.monotonic() - started, 1)
                report["samples"].append(item)
            except (OSError, ValueError, subprocess.SubprocessError, httpx.HTTPError) as exc:
                # 重启期间PID可能暂时不存在，不能丢掉整轮已采集的数据或伪装健康。
                report["sampling_failures"].append({"offset_seconds": round(time.monotonic() - started, 1),
                    "error_type": type(exc).__name__})
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            if index + 1 < args.samples:
                time.sleep(args.interval)
    finally:
        engine.dispose()
    report["status"] = "completed"
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"samples": len(report["samples"]), "output": str(args.output)}))


if __name__ == "__main__":
    main()
