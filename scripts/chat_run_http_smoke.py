"""最后部署版本的统一Run/SSE端到端冒烟，不输出凭据。"""

import argparse
import json
from chat_sse_client import chat_response, sse_events
from pathlib import Path
import sys

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def run(output):
    from stage46a_real_e2e import prepare_fixture, cleanup
    token, uid, pid, _, _ = prepare_fixture()
    try:
        with httpx.Client(base_url="http://127.0.0.1:32007", timeout=120,
                          headers={"Authorization": f"Bearer {token}"}) as client:
            created = client.post("/api/conversations", json={"title": "Chat最终部署冒烟", "project_id": pid,
                                  "model_name": "deepseek-ai/DeepSeek-V3.2"})
            created.raise_for_status()
            response = chat_response(client, "/api/chat/runs", json={"conversation_id": created.json()["id"],
                                   "content": "不调用工具。请只回答2+2的数值。"})
            response.raise_for_status()
            run_id = response.headers["x-chat-run-id"]
            events = sse_events(response.text)
            assert events[-1]["type"] == "done"
            snap = client.get(f"/api/chat/runs/{run_id}").json()
            answer = "".join(e["text"] for e in events if e["type"] == "answer_delta")
            assert snap["status"] == "succeeded" and snap["content"] == answer and "4" in answer
            replay = client.get(f"/api/chat/runs/{run_id}/events?after={snap['last_event_id']}")
            replay.raise_for_status()
            assert '"type": "run_snapshot"' in replay.text
            assert client.get(f"/api/chat/runs/{run_id}").json()["model_attempts"] == 1
            report = {"status": "passed", "sse_run_worker": True, "run_snapshot_matches_answer": True,
                "sse_tail_snapshot": True, "worker_final_generation_attempts": 1,
                "provider_finish_reason": next(e["finish_reason"] for e in events if e["type"] == "provider_finish")}
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report
    finally:
        cleanup(user_id=uid, project_id=pid)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    print(json.dumps(run(parser.parse_args().output), ensure_ascii=False))
