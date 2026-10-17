"""运维检查：依赖就绪、前端响应、五个受管进程；只输出状态，不打印连接凭据。"""

import argparse
import json
import subprocess
from pathlib import Path

import httpx


def inspect_services(*, backend: str, frontend: str) -> dict:
    checks = {}
    for name, url in (("backend", f"{backend}/health/ready"), ("frontend", f"{frontend}/"),
                      ("frontend_proxy", f"{frontend}/api/health/ready")):
        try:
            response = httpx.get(url, timeout=5, follow_redirects=True, trust_env=False)
            checks[name] = {"ok": response.status_code == 200}
            if name == "backend" and response.status_code in (200, 503):
                checks[name]["dependencies"] = response.json().get("checks", {})
        except (httpx.HTTPError, ValueError):
            checks[name] = {"ok": False}
    try:
        result = subprocess.run(
            ["bash", str(Path(__file__).with_name("run_services.sh")), "ctl", "status"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        states = {parts[0]: parts[1] for line in result.stdout.splitlines() if len(parts := line.split()) >= 2}
        expected = {"backend", "frontend", "knowledge-worker", "memory-worker", "durable-worker"}
        checks["processes"] = {
            # 外部 Redis 模式下可选 redis 进程为 STOPPED，ctl 会返回非零；只判断必需角色。
            "ok": all(states.get(name) == "RUNNING" for name in expected),
            "states": {name: states.get(name, "UNKNOWN") for name in sorted(expected)},
        }
    except (OSError, subprocess.TimeoutExpired):
        checks["processes"] = {"ok": False}
    return {"ok": all(value["ok"] for value in checks.values()), "checks": checks}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", default="http://127.0.0.1:32007")
    parser.add_argument("--frontend", default="http://127.0.0.1:32008")
    args = parser.parse_args()
    result = inspect_services(backend=args.backend.rstrip("/"), frontend=args.frontend.rstrip("/"))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    raise SystemExit(0 if result["ok"] else 1)
