"""在线模型+真实文件+进程退出+浏览器验收；只使用临时项目，不输出凭据。"""

import argparse
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

import httpx
from sqlalchemy import select, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def child(run_id):
    from app.core.database import SessionLocal, engine
    from app.services.chat_tool_runtime import ChatToolPersistence
    from app.services.chat_run_worker import ChatRunWorker
    from app.services.durable_checkpoint_service import checkpoint_pool_lifespan
    from app.services.approval_checkpoint_service import approval_pool_lifespan
    from app.services.provider_client_pool import start_provider_client_pool, close_provider_client_pool
    original = ChatToolPersistence._complete_call
    def stop_after_commit(self, step_id, result):
        original(self, step_id, result)
        if self.claim.run_id == run_id:
            os._exit(83)
    ChatToolPersistence._complete_call = stop_after_commit
    async def execute():
        start_provider_client_pool()
        try:
            with approval_pool_lifespan(engine.url):
                async with checkpoint_pool_lifespan(engine.url):
                    await ChatRunWorker(session_factory=SessionLocal, owner="chat-worker:e2e-crash").run_once()
        finally:
            await close_provider_client_pool()
    asyncio.run(execute())


def run(output):
    from playwright.sync_api import sync_playwright, expect
    from stage46a_real_e2e import prepare_fixture, cleanup
    from app.core.database import SessionLocal
    from app.models.agent_runtime import AgentArtifact, AgentCheckpoint, AgentRun, AgentStep
    from app.models.chat_run import ChatRun, ChatRunEvent
    from app.models.message import Message
    from app.services.tools import runtime_codec as codec

    token, uid, pid, fid, _ = prepare_fixture()
    client = httpx.Client(base_url="http://127.0.0.1:32007", timeout=30,
        headers={"Authorization": f"Bearer {token}"})
    report = {"scope": "真实在线DeepSeek-V3.2、PG原生检查点、文件工具、独立进程和浏览器；不启动Ollama", "cases": []}
    errors = []
    def save():
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    def ctl(*args):
        result = subprocess.run(["bash", str(ROOT / "deploy/run_services.sh"), "ctl", *args],
            cwd=ROOT, capture_output=True, text=True, timeout=45)
        if result.returncode:
            raise RuntimeError("测试服务控制失败")
        return result.stdout
    def snap(rid):
        response = client.get(f"/api/chat/runs/{rid}")
        response.raise_for_status()
        return response.json()
    def new_run(content, **kwargs):
        c = client.post("/api/conversations", json={"project_id": pid, "title": "Chat工具恢复真实验收",
            "model_name": "deepseek-ai/DeepSeek-V3.2"})
        c.raise_for_status()
        cid = c.json()["id"]
        response = client.post("/api/chat/runs", json={"conversation_id": cid, "content": content,
            "client_request_id": str(uuid4()), **kwargs})
        response.raise_for_status()
        return cid, response.json()["id"]
    def terminal(rid, limit=240):
        end = time.monotonic() + limit
        while time.monotonic() < end:
            row = snap(rid)
            if row["status"] not in ("queued", "running", "cancel_requested"):
                return row
            time.sleep(.3)
        raise AssertionError("恢复任务未在验收时限内结束")
    def browser_page(p, cid):
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1000}, permissions=["clipboard-read", "clipboard-write"])
        context.add_cookies([{"name": "aiws_token", "value": token, "url": "http://127.0.0.1:32008",
            "httpOnly": True, "sameSite": "Lax"}])
        page = context.new_page()
        page.on("pageerror", lambda _: errors.append("pageerror"))
        page.on("console", lambda m: errors.append("console_error") if m.type == "error" else None)
        page.goto(f"http://127.0.0.1:32008/chat?conversation={cid}", wait_until="domcontentloaded")
        expect(page.get_by_placeholder("输入你的问题")).to_be_visible(timeout=30000)
        return browser, page
    try:
        with SessionLocal() as db:
            active = db.scalar(text("SELECT count(*) FROM chat_runs WHERE status IN ('queued','running','cancel_requested')"))
        assert active == 0, "存在其他用户任务，不能进行Worker故障测试"
        ctl("stop", "chat-worker")
        cid, rid = new_run("仅阅读当前项目中的stage46a-readme.md。请先用文件搜索工具定位，再读取文件正文，"
            "逐字引用它的第一行，并基于正文说明文件用途和安全边界。不要联网，不修改任何文件。",
            web_search_enabled=True, tool_run_mode="guided_research")
        result = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--child", rid],
            cwd=ROOT, capture_output=True, timeout=110)
        report["child_exit_code"] = result.returncode
        save()
        assert result.returncode == 83, "子进程未在真实工具结果提交后退出"
        with SessionLocal() as db:
            agent = db.scalar(select(AgentRun).where(AgentRun.chat_run_id == rid))
            first = db.scalar(select(AgentStep).where(AgentStep.run_id == agent.id, AgentStep.sequence > 0,
                AgentStep.status == "succeeded").order_by(AgentStep.sequence))
            assert first, "退出前没有已提交的工具步骤"
            first_id, first_key = first.id, first.tool_key
        # 不修改租约时间：等待真实45秒租约过期，由正式Worker自动发现和接管。
        ctl("start", "chat-worker")
        with sync_playwright() as p:
            browser, page = browser_page(p, cid)
            try:
                final = terminal(rid)
                report["tool_case_latest"] = {"status": final["status"], "error_code": final["error_code"], "answer": final["content"]}
                save()
                assert final["status"] == "succeeded", final["error_code"]
                assert "阶段 4.6A 临时文件" in final["content"] and "只读" in final["content"]
                with SessionLocal() as db:
                    first = db.get(AgentStep, first_id)
                    assert first.attempts == 1, "已提交步骤被重新执行"
                    agent = db.scalar(select(AgentRun).where(AgentRun.chat_run_id == rid))
                    steps = list(db.scalars(select(AgentStep).where(AgentStep.run_id == agent.id, AgentStep.sequence > 0)))
                    assert {"workspace.files.search", "workspace.files.read"}.issubset({s.tool_key for s in steps})
                    assert db.get(ChatRun, rid).lease_version == 2
                    assert db.get(Message, final["assistant_message_id"]).content == final["content"]
                expect(page.locator("article.message-assistant .chat-markdown").last).to_contain_text("临时文件", timeout=30000)
                page.locator("section.chat-surface").screenshot(path=str(output.with_suffix(".tool-recovered.png")))
                report["cases"].append({"case": "真实工具提交后进程退出、真实租约到期、接管并完成回答", "status": "passed",
                    "reused_tool": first_key, "saved_step_attempts": first.attempts,
                    "chat_worker_attempts": 2, "final_generation_attempts": final["model_attempts"], "final_chars": len(final["content"])})
                save()
            finally:
                browser.close()

            # 在真实浏览器展示部分回答时，强制终止唯一ChatWorker，正式Supervisor自动拉起。
            cid2, rid2 = new_run("Do not use tools. Write a plain-text tutorial of about 1500 English words on "
                "PostgreSQL transactions, with eight numbered sections and practical examples. Do not use Markdown formatting.")
            browser, page = browser_page(p, cid2)
            try:
                end = time.monotonic() + 90
                while time.monotonic() < end:
                    before = snap(rid2)
                    visible = page.locator("article.message-assistant .chat-markdown").last
                    if len(before["content"]) > 700 and before["status"] == "running" and visible.count() and len(visible.inner_text()) > 400:
                        break
                    page.wait_for_timeout(150)
                assert before["status"] == "running" and len(before["content"]) > 700, "未收到足够的真实部分回答"
                prefix = before["content"]
                page.locator("section.chat-surface").screenshot(path=str(output.with_suffix(".before-worker-crash.png")))
                with SessionLocal() as db:
                    assert db.scalar(text("SELECT count(*) FROM chat_runs WHERE status IN ('queued','running','cancel_requested')")) == 1
                ctl("signal", "KILL", "chat-worker")
                final = terminal(rid2)
                report["generation_case_latest"] = {"status": final["status"], "error_code": final["error_code"], "answer": final["content"]}
                save()
                assert final["status"] == "succeeded" and final["model_attempts"] == 2
                with SessionLocal() as db:
                    agent = db.scalar(select(AgentRun).where(AgentRun.chat_run_id == rid2))
                    old = db.scalar(select(AgentArtifact).where(AgentArtifact.run_id == agent.id,
                        AgentArtifact.artifact_type == "chat_generation_partial"))
                    archived = codec.loads(old.content_json)
                    assert archived["content"].startswith(prefix)
                    events = [json.loads(e.event_json) for e in db.scalars(select(ChatRunEvent).where(
                        ChatRunEvent.run_id == rid2).order_by(ChatRunEvent.sequence))]
                    reset = [e for e in events if e.get("reason") == "generation_restarted"]
                    assert len(reset) == 1 and reset[0]["run"]["content"] == ""
                    message = db.get(Message, final["assistant_message_id"])
                    assert message.content == final["content"] and message.status == "done"
                visible = page.locator("article.message-assistant .chat-markdown").last
                expect(visible).to_be_visible(timeout=30000)
                page.wait_for_timeout(1500)
                page.locator("section.chat-surface").screenshot(path=str(output.with_suffix(".generation-recovered.png")))
                report["generation_case_latest"]["browser_rendered_text"] = visible.inner_text()
                # Markdown的序号/代码样式会改变innerText；点击真实复制按钮核对前端持有的完整原文。
                copy = page.locator("article.message-assistant").last.get_by_role("button", name="复制回答", exact=True)
                expect(copy).to_be_visible(timeout=30000)
                copy.click()
                copied = page.evaluate("navigator.clipboard.readText()")
                report["generation_case_latest"]["browser_copied_answer"] = copied
                save()
                assert copied == final["content"], "浏览器持有的正文与重新生成的完整结果不一致"
                report["cases"].append({"case": "真实流式输出后KILL Worker、自动拉起、回答重置并完成", "status": "passed",
                    "chars_before_crash": len(prefix), "archived_chars": len(archived["content"]),
                    "final_chars": len(final["content"]), "final_generation_attempts": final["model_attempts"],
                    "browser_matches_new_answer": True, "reset_events": len(reset)})
                assert not errors, "浏览器出现错误"
                report["browser_errors"] = errors
                report["status"] = "passed"
                save()
            finally:
                browser.close()
    except BaseException as error:
        report["status"], report["error_type"] = "failed", type(error).__name__
        save()
        raise
    finally:
        # 故障验收退出后恢复唯一托管Worker；不留下第二个临时执行进程。
        subprocess.run(["bash", str(ROOT / "deploy/run_services.sh"), "ctl", "start", "chat-worker"],
            cwd=ROOT, capture_output=True, timeout=45)
        client.close()
        cleanup(user_id=uid, project_id=pid)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--child")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.child:
        child(args.child)
    else:
        if args.output is None:
            parser.error("需指定--output")
        run(args.output)
