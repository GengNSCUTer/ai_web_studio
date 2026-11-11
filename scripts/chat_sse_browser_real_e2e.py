"""原生EventSource的真实浏览器操作、自动续订与数据库核对；不打印任何凭据。"""

import json
import os
import re
import signal
import subprocess
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

import httpx
from playwright.sync_api import sync_playwright, expect
from sqlalchemy import select, delete

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from stage46a_real_e2e import prepare_fixture, cleanup
from app.core.database import SessionLocal
from app.models.user_setting import UserSetting
from app.models.message import Message
from app.models.chat_run import ChatRun
from app.models.conversation import Conversation
from app.models.user_memory import MemoryExtractionJob, UserMemory

WEB = os.getenv("AIWS_E2E_BASE_URL", "http://127.0.0.1:32008")
API = os.getenv("AIWS_E2E_BACKEND_URL", "http://127.0.0.1:32007")


def run():
    token, user_id, project_id, _, _ = prepare_fixture()
    with SessionLocal() as db:
        model = db.scalar(select(UserSetting.default_model).where(UserSetting.user_id == user_id))
    client = httpx.Client(base_url=API, headers={"authorization": f"Bearer {token}"}, timeout=180)
    errors, requests, cases, runs = [], [], [], []
    network_fault = {"injected": False, "expected_errors": 0}
    try:
        created = client.post("/api/conversations", json={"project_id": project_id, "title": "统一SSE浏览器验收", "model_name": model})
        created.raise_for_status()
        conv = created.json()["id"]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            context.add_init_script("""(() => {
                const NativeEventSource = window.EventSource;
                window.__nativeEventSourceCount = 0;
                window.EventSource = class extends NativeEventSource {
                    constructor(...args) { super(...args); window.__nativeEventSourceCount += 1; }
                };
            })();""")
            context.add_cookies([{"name": "aiws_token", "value": token, "url": WEB, "httpOnly": True, "sameSite": "Lax"}])
            page = context.new_page()
            page.on("pageerror", lambda _: errors.append("pageerror"))
            def console_message(message):
                if message.type != "error":
                    return
                if network_fault["injected"] and any(code in message.text for code in (
                    "net::ERR_INCOMPLETE_CHUNKED_ENCODING", "net::ERR_CONNECTION_RESET",
                    "net::ERR_CONNECTION_REFUSED", "net::ERR_EMPTY_RESPONSE", "net::ERR_ABORTED")):
                    network_fault["expected_errors"] += 1
                else:
                    errors.append("console_error")
            page.on("console", console_message)
            page.on("request", lambda r: requests.append({"method": r.method, "path": urlsplit(r.url).path,
                        "resource_type": r.resource_type, "last_event_id": r.all_headers().get("last-event-id")
                            if r.resource_type == "eventsource" else None})
                    if "/chat/" in r.url or r.url.endswith("/api/chat") else None)
            page.goto(WEB + f"/chat?conversation={conv}", wait_until="networkidle")

            def action(name, suffix, callback, expected, after_created=None):
                path = "/api/backend/chat/runs" + suffix
                with page.expect_response(lambda r: urlsplit(r.url).path == path and r.request.method == "POST", timeout=30000) as pending:
                    callback()
                response = pending.value
                assert response.status == 202 and response.headers.get("content-type", "").startswith("application/json")
                run = response.json()
                runs.append(run["id"])
                if after_created:
                    after_created(run)
                deadline = time.monotonic() + 150
                while time.monotonic() < deadline:
                    snapshot = client.get(f"/api/chat/runs/{run['id']}").json()
                    if snapshot["status"] in {"succeeded", "failed", "cancelled", "expired", "interrupted"}:
                        break
                    page.wait_for_timeout(100)
                assert snapshot["status"] == "succeeded", f"{name}未成功: status={snapshot['status']}, error_code={snapshot.get('error_code')}"
                assert expected in snapshot["content"]
                expect(page.locator("article.message-assistant .chat-markdown").last).to_contain_text(expected, timeout=20000)
                expect(page.get_by_role("button", name="停止生成", exact=True)).to_have_count(0, timeout=20000)
                with SessionLocal() as db:
                    message = db.get(Message, snapshot["assistant_message_id"])
                    self_run = db.get(ChatRun, run["id"])
                    assert message.status == "done" and message.content == snapshot["content"]
                    assert message.generation_id == self_run.generation_id and self_run.model_attempts == 1
                cases.append({"case": name, "status": "passed", "create_status": 202, "model_attempts": 1,
                              "database_matches": True, "answer_chars": len(snapshot["content"])})
                return run

            page.get_by_placeholder("输入你的问题").fill("不要使用工具，只回复 SSE_FIRST_OK。")
            initial = action("新问答", "", lambda: page.get_by_role("button", name="发送消息", exact=True).click(), "SSE_FIRST_OK")
            page.locator("article.message-assistant").last.hover()
            regenerated = action("重新生成", "/regenerate", lambda: page.get_by_role("button", name="重新生成", exact=True).click(), "SSE_FIRST_OK")
            assert initial["assistant_message_id"] == regenerated["assistant_message_id"]
            page.get_by_role("button", name="编辑后重答", exact=True).click()
            page.get_by_placeholder("输入你的问题").first.fill("不要使用工具，只回复 SSE_EDIT_OK。")
            edited = action("编辑重答", "/edit", lambda: page.get_by_role("button", name="保存并重答", exact=True).click(), "SSE_EDIT_OK")
            assert initial["assistant_message_id"] == edited["assistant_message_id"]
            with SessionLocal() as db:
                assert "SSE_EDIT_OK" in db.get(Message, edited["user_message_id"]).content
                generations = [db.get(ChatRun, run_id).generation_id for run_id in runs]
                assert len(set(generations)) == 3
            # 刷新只查询结果，不重新创建生成任务。
            count = len([r for r in requests if r["method"] == "POST"])
            page.reload(wait_until="networkidle")
            expect(page.locator("article.message-assistant .chat-markdown").last).to_contain_text("SSE_EDIT_OK")
            assert len([r for r in requests if r["method"] == "POST"]) == count
            assert not any(r["path"].startswith("/api/chat") and not r["path"].startswith("/api/chat/runs") for r in requests)
            subscriptions = [r for r in requests if r["method"] == "GET" and r["path"].endswith("/events")]
            assert len(subscriptions) >= 3 and all(r["resource_type"] == "eventsource" for r in subscriptions)

            # 强制结束独立前端进程组切断真实连接；温和重启可能保留活跃流到结束。
            observed = []
            cdp = context.new_cdp_session(page)
            cdp.send("Network.enable")
            cdp.on("Network.eventSourceMessageReceived", lambda event: observed.append(event.get("eventId", "")))
            sources_before = page.evaluate("window.__nativeEventSourceCount")
            def interrupt_connection(run):
                end = time.monotonic() + 90
                while time.monotonic() < end:
                    content = page.locator("article.message-assistant .chat-markdown").last.inner_text()
                    if len(content) >= 80 and any(value.startswith(run["id"] + ":") for value in observed):
                        break
                    page.wait_for_timeout(100)
                assert len(content) >= 80 and client.get(f"/api/chat/runs/{run['id']}").json()["status"] == "running", "断网前没有实际生成正文"
                network_fault["injected"] = True
                process = subprocess.run(["bash", "deploy/run_services.sh", "ctl", "pid", "frontend"],
                    cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=30)
                assert process.returncode == 0, "无法确认前端进程"
                frontend_pid = int(process.stdout.strip())
                root = Path(__file__).resolve().parents[1]
                assert os.getpgid(frontend_pid) == frontend_pid, "前端没有独立进程组，拒绝故障注入"
                assert Path(f"/proc/{frontend_pid}/cwd").resolve() == root / "frontend", "目标不属于当前项目"
                os.killpg(frontend_pid, signal.SIGKILL)

            page.get_by_placeholder("输入你的问题").fill(
                "Do not use tools. In a single plain-text paragraph without Markdown, write a detailed 250-word explanation "
                "of how database transactions work. End with exactly SSE_NATIVE_RECONNECT_OK.")
            recovered = action("原生自动续订", "", lambda: page.get_by_role("button", name="发送消息", exact=True).click(),
                "SSE_NATIVE_RECONNECT_OK", after_created=interrupt_connection)
            with SessionLocal() as db:
                expected_answer = db.get(Message, recovered["assistant_message_id"]).content
            visible_answer = page.locator("article.message-assistant .chat-markdown").last.inner_text()
            assert re.sub(r"\s+", " ", visible_answer).strip() == re.sub(r"\s+", " ", expected_answer).strip(), "重连后页面正文与数据库不一致"
            subscription_path = f"/api/backend/chat/runs/{recovered['id']}/events"
            reconnects = [r for r in requests if r["path"] == subscription_path]
            assert len(reconnects) >= 2 and any((r["last_event_id"] or "").startswith(recovered["id"] + ":") for r in reconnects[1:]), json.dumps({"error": "原生重连未发送Last-Event-ID", "requests": reconnects, "observed_count": len(observed)}, ensure_ascii=False)
            assert page.evaluate("window.__nativeEventSourceCount") == sources_before + 1, "不应由应用重建EventSource续订"
            count = len(reconnects)
            page.wait_for_timeout(3500)
            assert len([r for r in requests if r["path"] == subscription_path]) == count, "终态没有关闭EventSource"
            cdp.detach()
            assert not errors
            page.screenshot(path="/tmp/aiws-chat-sse-browser.png", full_page=True)
            browser.close()
        report = {"status": "passed", "model": model, "cases": cases, "browser_errors": 0,
            "same_assistant_three_generation_ids": True, "refresh_no_new_run": True,
            "native_eventsource": True, "automatic_last_event_id": True, "proxy_cursor_forwarded": True,
            "resumed_ui_matches_database": True, "terminal_closed": True,
            "single_eventsource_instance_reconnected": True,
            "expected_transport_console_errors": network_fault["expected_errors"],
            "reconnect_scope": "SIGKILL独立前端组、Supervisor自动重启，Chromium同一原生对象续订；Worker不重启、非伪造响应",
            "wire_protocol": "POST_JSON_create_native_EventSource_GET_SSE", "scope": "真实在线模型/浏览器/独立Worker/PostgreSQL"}
        output = Path(os.getenv("AIWS_SSE_BROWSER_REPORT", "/tmp/aiws-chat-sse-browser-real.json"))
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
    finally:
        for run_id in runs:
            client.post(f"/api/chat/runs/{run_id}/cancel")
        client.close()
        with SessionLocal() as db:
            ids = list(db.scalars(select(Conversation.id).where(Conversation.project_id == project_id)))
            db.execute(delete(UserMemory).where(UserMemory.user_id == user_id, UserMemory.project_id == project_id))
            db.execute(delete(MemoryExtractionJob).where(MemoryExtractionJob.conversation_id.in_(ids)))
            db.commit()
        cleanup(user_id=user_id, project_id=project_id)


if __name__ == "__main__":
    run()
