"""在线模型、真实浏览器关闭/重开、SSE重放、停止和文件工具端到端验收。"""

import argparse
import json
from pathlib import Path
import sys
import subprocess
import time
from uuid import uuid4

import httpx
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def run(output):
    from playwright.sync_api import sync_playwright, expect
    import tiktoken
    from stage46a_real_e2e import prepare_fixture, cleanup
    from app.core.database import SessionLocal
    from app.models.chat_run import ChatRun
    from app.models.message import Message
    from app.models.observability import ChatRuntimeMetric
    from app.models.tool_trace import ToolCallRun, ToolRouteRun
    from app.models.user_setting import UserSetting

    token, uid, pid, _, _ = prepare_fixture()
    with SessionLocal() as db:
        model = db.scalar(select(UserSetting.default_model).where(UserSetting.user_id == uid))
    report = {"scope": "账号当前在线模型、唯一托管ChatWorker、浏览器和PostgreSQL；不调用Ollama", "model": model, "cases": []}
    errors, run_ids = [], []
    client = httpx.Client(base_url="http://127.0.0.1:32007", timeout=240,
                          headers={"Authorization": f"Bearer {token}"})

    def save():
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    def conversation(title):
        response = client.post("/api/conversations", json={"project_id": pid, "title": title,
                              "model_name": model})
        response.raise_for_status()
        return response.json()["id"]

    def snapshot(run_id):
        response = client.get(f"/api/chat/runs/{run_id}")
        response.raise_for_status()
        return response.json()

    def until_terminal(run_id, seconds=240):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            snap = snapshot(run_id)
            if snap["status"] not in ("queued", "running", "cancel_requested"):
                return snap
            time.sleep(.3)
        raise AssertionError("任务未按时结束")

    def browser_page(playwright, cid):
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000})
        context.add_cookies([{"name": "aiws_token", "value": token, "url": "http://127.0.0.1:32008",
                             "httpOnly": True, "sameSite": "Lax"}])
        page = context.new_page()
        page.on("pageerror", lambda _: errors.append("pageerror"))
        page.on("console", lambda m: errors.append("console_error") if m.type == "error" else None)
        # 活跃SSE会一直占用网络，使用真实输入框就绪替代永远达不到的networkidle。
        page.goto(f"http://127.0.0.1:32008/chat?conversation={cid}", wait_until="domcontentloaded")
        expect(page.get_by_placeholder("输入你的问题")).to_be_visible(timeout=30000)
        return browser, page

    try:
        cid = conversation("ChatRun关闭浏览器验收")
        with sync_playwright() as p:
            browser, page = browser_page(p, cid)
            try:
                page.get_by_placeholder("输入你的问题").fill(
                    "Do not use any tools. Write a detailed 600-word English tutorial about PostgreSQL transactions, "
                    "with 12 numbered sections and concrete examples. At the very end write exactly: CHAT_RUN_COMPLETE_20261108")
                page.get_by_role("button", name="发送消息", exact=True).click()
                end, run_id = time.monotonic() + 60, None
                while time.monotonic() < end:
                    with SessionLocal() as db:
                        row = db.scalar(select(ChatRun).where(ChatRun.conversation_id == cid).order_by(ChatRun.created_at.desc()))
                        if row:
                            run_id = row.id
                            break
                    page.wait_for_timeout(200)
                assert run_id, "浏览器未创建ChatRun"
                run_ids.append(run_id)
                enc = tiktoken.get_encoding("cl100k_base")
                end, received = time.monotonic() + 120, 0
                while time.monotonic() < end:
                    snap = snapshot(run_id)
                    received = len(enc.encode(snap["content"]))
                    visible = page.locator("article.message-assistant .chat-markdown").last
                    visible_tokens = len(enc.encode(visible.inner_text())) if visible.count() else 0
                    if received >= 150 and visible_tokens >= 100 and snap["status"] == "running":
                        break
                    assert snap["status"] not in ("failed", "expired", "interrupted"), snap["error_code"]
                    page.wait_for_timeout(150)
                assert received >= 100 and visible_tokens >= 100 and snap["status"] == "running", "关闭时浏览器未显示至少100Token或任务已经结束"
                partial = snap["content"]
                page.locator("section.chat-surface").screenshot(path=str(output.with_suffix(".before-close.png")))
            finally:
                # 完整关闭浏览器，既不是隐藏页面，也不是只断开Playwright控制。
                browser.close()
            # 同一验收再重启Web，执行权仍在独立Worker；不重启或补发生成任务。
            restart = subprocess.run(["bash", str(ROOT / "deploy/run_services.sh"), "ctl", "restart", "backend"],
                cwd=ROOT, capture_output=True, timeout=30)
            assert restart.returncode == 0, "Web重启失败"
            final = until_terminal(run_id)
            report["latest_attempt"] = {"case": "关闭浏览器后完成", "status": final["status"],
                "error_code": final["error_code"], "answer": final["content"]}
            with SessionLocal() as db:
                metric = db.scalar(select(ChatRuntimeMetric).where(ChatRuntimeMetric.assistant_message_id == final["assistant_message_id"]))
                stats = json.loads(metric.stats_json) if metric else {}
                report["latest_attempt"]["provider_finish_reason"] = stats.get("provider_finish_reason")
                report["latest_attempt"]["output_tokens"] = stats.get("provider_output_tokens")
                report["latest_attempt"]["output_limit"] = stats.get("chat_run_output_token_limit")
            save()
            assert final["status"] == "succeeded", final["error_code"]
            assert final["content"].startswith(partial) and len(final["content"]) > len(partial)
            assert "CHAT_RUN_COMPLETE_20261108" in final["content"]
            attempts = final["model_attempts"]
            browser, page = browser_page(p, cid)
            try:
                expect(page.get_by_text("CHAT_RUN_COMPLETE_20261108", exact=False).first).to_be_visible(timeout=30000)
                page.locator("section.chat-surface").screenshot(path=str(output.with_suffix(".reopened.png")))
                page.wait_for_timeout(1000)
                assert snapshot(run_id)["model_attempts"] == attempts == 1
                with SessionLocal() as db:
                    row = db.get(Message, final["assistant_message_id"])
                    assert row.status == "done" and row.content == final["content"]
                report["cases"].append({"case": "收到100Token后彻底关闭浏览器并重开", "status": "passed",
                    "received_cl100k_tokens_before_close": received, "chars_before_close": len(partial),
                    "browser_visible_cl100k_tokens_before_close": visible_tokens,
                    "final_chars": len(final["content"]), "worker_final_generation_attempts": attempts,
                    "content_matches_database": True, "complete_after_browser_closed": True,
                    "web_restarted_without_restarting_worker": True})
                report["latest_attempt"]["runtime_verified"] = True
                save()
            finally:
                browser.close()

            # 活跃Run重开会话会自动SSE续订；真实停止按钮走独立取消接口。
            cid2 = conversation("ChatRun重连与停止验收")
            response = client.post("/api/chat/runs", json={"conversation_id": cid2, "client_request_id": str(uuid4()),
                "content": "不调用工具。请写一篇至少3000字的分布式事务入门，逐项详细解释并给例子。"})
            response.raise_for_status()
            rid2 = response.json()["id"]
            run_ids.append(rid2)
            browser, page = browser_page(p, cid2)
            try:
                with page.expect_response(lambda r: f"/chat/runs/{rid2}/events" in r.url, timeout=30000):
                    page.reload(wait_until="domcontentloaded")
                expect(page.get_by_role("button", name="停止生成", exact=True)).to_be_visible(timeout=90000)
                end = time.monotonic() + 90
                while time.monotonic() < end and not snapshot(rid2)["content"]:
                    page.wait_for_timeout(200)
                before = snapshot(rid2)
                assert before["content"] and before["status"] == "running", "停止前未实际生成"
                page.get_by_role("button", name="停止生成", exact=True).click()
                stopped = until_terminal(rid2, 30)
                assert stopped["status"] == "cancelled" and stopped["content"]
                repeat = client.post(f"/api/chat/runs/{rid2}/cancel")
                repeat.raise_for_status()
                assert repeat.json()["last_event_id"] == stopped["last_event_id"]
                expect(page.get_by_role("button", name="发送消息", exact=True)).to_be_visible(timeout=30000)
                report["cases"].append({"case": "活跃任务SSE重连/真实停止/重复停止", "status": "passed",
                    "final_status": stopped["status"], "partial_chars": len(stopped["content"]),
                    "model_attempts": stopped["model_attempts"], "duplicate_cancel_no_new_event": True})
                save()
            finally:
                browser.close()

        # 同一会话取消后可继续发送，文件工具和最终回答仍走原LangGraph/Executor。
        response = client.post("/api/chat/runs", json={"conversation_id": cid2,
            "content": "只读：请搜索并读取stage46a-readme.md，告诉我第一行和用途；不联网不修改。",
            "web_search_enabled": True, "skill_key": "workspace.document-review", "tool_run_mode": "workspace_review"})
        response.raise_for_status()
        tool_run = response.json()["id"]
        run_ids.append(tool_run)
        tool_final = until_terminal(tool_run)
        assert tool_final["status"] == "succeeded", tool_final["error_code"]
        assert "阶段 4.6A 临时文件" in tool_final["content"] and "验证" in tool_final["content"]
        with SessionLocal() as db:
            route = db.scalar(select(ToolRouteRun).where(ToolRouteRun.assistant_message_id == tool_final["assistant_message_id"]))
            calls = list(db.scalars(select(ToolCallRun).where(ToolCallRun.route_run_id == route.id)))
            keys = {row.tool_key for row in calls}
            assert {"workspace.files.search", "workspace.files.read"}.issubset(keys)
            metric = db.scalar(select(ChatRuntimeMetric).where(ChatRuntimeMetric.assistant_message_id == tool_final["assistant_message_id"]))
            stats = json.loads(metric.stats_json)
            assert stats["chat_stream_orchestrator"] == "langgraph"
        # SSE尾游标再次订阅只拿快照，不触发生成。
        replay = client.get(f"/api/chat/runs/{tool_run}/events", headers={"Last-Event-ID": f"{tool_run}:{tool_final['last_event_id']}"})
        replay.raise_for_status()
        assert '"type": "run_snapshot"' in replay.text
        assert snapshot(tool_run)["model_attempts"] == 1
        report["cases"].append({"case": "取消后新发/真实文件工具/最终回答/尾游标重放", "status": "passed",
            "tools": sorted(keys), "answer_chars": len(tool_final["content"]), "langgraph": True})
        assert not errors, "浏览器出现控制台或页面错误"
        report["browser_errors"] = errors
        report["status"] = "passed"
        save()
        return report
    except Exception as exc:
        report["status"] = "failed"
        report["error_type"] = type(exc).__name__
        report["run_states"] = [{"status": snapshot(r)["status"], "phase": snapshot(r)["phase"],
            "error_code": snapshot(r)["error_code"]} for r in run_ids]
        save()
        raise
    finally:
        for rid in run_ids:
            try:
                client.post(f"/api/chat/runs/{rid}/cancel")
                until_terminal(rid, 15)
            except Exception:
                pass
        client.close()
        cleanup(user_id=uid, project_id=pid)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.output), ensure_ascii=False, indent=2))
