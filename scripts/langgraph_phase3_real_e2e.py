"""在线Chat→浏览器确认→关闭浏览器→独立Worker→PG检查点/产物/结果回传验收。"""

import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

import httpx
from sqlalchemy import delete, select, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def ctl(action):
    result = subprocess.run(["bash", str(ROOT / "deploy/run_services.sh"), "ctl", action, "durable-worker"],
                            cwd=ROOT, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"Durable Worker {action}失败")


def run(output):
    from playwright.sync_api import sync_playwright, expect
    from stage46a_real_e2e import prepare_fixture, cleanup
    from app.core.database import SessionLocal
    from app.models.agent_runtime import AgentArtifact, AgentRun, AgentStep
    from app.models.message import Message

    report = {"scope": "真实在线模型、浏览器、独立托管Worker、生产同库临时项目；只读任务"}
    token, uid, pid, _, _ = prepare_fixture()
    run_id, worker_stopped = None, False
    client = httpx.Client(base_url="http://127.0.0.1:32007",
        headers={"Authorization": f"Bearer {token}"}, timeout=180)
    try:
        response = client.post("/api/conversations", json={"title": "LangGraph3后台验收", "project_id": pid,
                                                          "model_name": "deepseek-ai/DeepSeek-V3.2"})
        response.raise_for_status()
        cid = response.json()["id"]
        response = client.post("/api/chat/events-stream", json={"conversation_id": cid,
            "content": "在工作区先搜索再读取stage46a-readme.md，告诉我第一行原文和用途；只读文件，不联网不修改。",
            "web_search_enabled": True, "skill_key": "workspace.document-review", "tool_run_mode": "workspace_review"})
        response.raise_for_status()
        events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
        assert events[-1]["type"] == "done", "在线Chat未完成"
        assert any(event["type"] == "tool_call_end" for event in events), "未真实执行文件工具"
        with SessionLocal() as db:
            assert db.scalar(select(AgentRun).where(AgentRun.status.in_(["queued", "running"])).limit(1)) is None, \
                "已有其他后台任务，本测试不暂停Worker"
        # 只暂停本项目唯一Durable角色，确保确认时真正排队；finally恢复，不启动第二套服务。
        ctl("stop")
        worker_stopped = True
        errors = []
        preview_request = {}
        handoff_token = None
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                context.add_cookies([{"name": "aiws_token", "value": token, "url": "http://127.0.0.1:32008",
                                     "httpOnly": True, "sameSite": "Lax"}])
                page = context.new_page()
                page.on("pageerror", lambda _: errors.append("pageerror"))
                page.on("console", lambda m: errors.append("console") if m.type == "error" else None)
                page.goto("http://127.0.0.1:32008/chat", wait_until="networkidle")
                page.locator("select").nth(0).select_option(pid)
                page.get_by_text("LangGraph3后台验收", exact=True).last.click()
                panel = page.locator("details.reasoning-panel").filter(
                    has=page.get_by_role("button", name="预览后台重执行", exact=True, include_hidden=True)).first
                panel.locator(":scope > summary").click()
                with page.expect_response(lambda r: r.url.endswith("/tool-handoffs/preview")) as preview_response:
                    page.get_by_role("button", name="预览后台重执行", exact=True).last.click()
                preview = preview_response.value
                assert preview.ok
                preview_request = preview.request.post_data_json
                handoff_token = preview.json()["handoff_token"]
                with SessionLocal() as db:
                    assert db.scalar(select(AgentRun).where(AgentRun.project_id == pid).limit(1)) is None, \
                        "预览不应该创建Run"
                with page.expect_response(lambda r: r.url.endswith("/tool-handoffs/confirm")) as confirm_response:
                    page.get_by_role("button", name="确认并后台执行", exact=True).last.click()
                confirm = confirm_response.value
                assert confirm.ok
                run_id = confirm.json()["id"]
                expect(page.get_by_text("Durable Run：queued", exact=True)).to_be_visible(timeout=20000)
                assert not errors, "浏览器出现错误"
            finally:
                browser.close()

        ctl("start")
        worker_stopped = False
        deadline = time.monotonic() + 30
        snapshot = None
        while time.monotonic() < deadline:
            response = client.get(f"/api/agent-runtime/runs/{run_id}")
            response.raise_for_status()
            snapshot = response.json()
            if snapshot["run"]["status"] not in {"queued", "running"}:
                break
            time.sleep(0.2)
        assert snapshot["run"]["status"] == "succeeded", "浏览器关闭后任务未完成"
        assert snapshot["artifacts"] and all(step["status"] == "succeeded" for step in snapshot["steps"])
        # 相同令牌重复确认、重新预览再确认，都复用同一Run。
        repeat = client.post("/api/agent-runtime/tool-handoffs/confirm", json={"handoff_token": handoff_token})
        repeat.raise_for_status()
        assert repeat.json()["id"] == run_id
        fresh_preview = client.post("/api/agent-runtime/tool-handoffs/preview", json=preview_request)
        fresh_preview.raise_for_status()
        fresh_confirm = client.post("/api/agent-runtime/tool-handoffs/confirm",
                                    json={"handoff_token": fresh_preview.json()["handoff_token"]})
        fresh_confirm.raise_for_status()
        assert fresh_confirm.json()["id"] == run_id
        with SessionLocal() as db:
            run_row = db.get(AgentRun, run_id)
            state = json.loads(run_row.planner_state_json)
            assert state["runtime_engine"] == "langgraph_tool_v1"
            result_message = db.get(Message, state["durable_result_message_id"])
            assert result_message.conversation_id == cid and result_message.status == "done"
            assert "stage46a-readme.md" in result_message.content
            checkpoints = db.execute(text("SELECT c.checkpoint FROM aiws_graph.checkpoints c "
                "JOIN aiws_graph.runtime_threads t ON t.thread_id=c.thread_id WHERE t.run_id=:rid "
                "ORDER BY c.checkpoint_id DESC"), {"rid": run_id}).all()
            assert checkpoints and checkpoints[0][0]["channel_values"]["phase"] == "reconciled"
            assert all(step.attempts == 1 for step in db.scalars(select(AgentStep).where(AgentStep.run_id == run_id)))
            report.update({"run_status": run_row.status, "runtime_engine": state["runtime_engine"],
                "native_checkpoint_count": len(checkpoints), "steps": len(snapshot["steps"]),
                "artifacts": len(snapshot["artifacts"]), "browser_errors": errors,
                "preview_created_run": False, "browser_closed_while_queued": True,
                "duplicate_confirm_reused_run": True, "result_projected_to_original_conversation": True})
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context()
                context.add_cookies([{"name": "aiws_token", "value": token, "url": "http://127.0.0.1:32008",
                                     "httpOnly": True, "sameSite": "Lax"}])
                page = context.new_page()
                page.goto("http://127.0.0.1:32008/chat", wait_until="networkidle")
                page.locator("select").nth(0).select_option(pid)
                page.get_by_text("LangGraph3后台验收", exact=True).last.click()
                expect(page.get_by_text(f"后台任务已完成（Run {run_id}）", exact=False).first).to_be_visible(timeout=20000)
                report["result_visible_after_reopening_browser"] = True
            finally:
                browser.close()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report
    except Exception as exc:
        report["failed"] = {"error_type": type(exc).__name__, "run_created": run_id is not None}
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        raise
    finally:
        if worker_stopped:
            ctl("start")
        client.close()
        with SessionLocal() as db:
            db.execute(delete(AgentRun).where(AgentRun.project_id == pid, AgentRun.user_id == uid))
            db.commit()
            assert db.execute(text("SELECT count(*) FROM aiws_graph.runtime_threads WHERE run_id=:rid"),
                              {"rid": run_id}).scalar() == 0
        cleanup(user_id=uid, project_id=pid)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.output), ensure_ascii=False, indent=2))
