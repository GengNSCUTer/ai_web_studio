"""阶段 4.9：真实数据库、独立 Worker 进程和浏览器任务页烟测。"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright
from sqlalchemy import delete, func, select


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.database import SessionLocal  # noqa: E402
from app.models.agent_runtime import (  # noqa: E402
    AgentArtifact, AgentCheckpoint, AgentOutboxEvent, AgentRun, AgentStep, AgentWorkerHeartbeat,
)
from app.models.conversation import Conversation  # noqa: E402
from app.models.message import Message  # noqa: E402
from app.models.project import Project  # noqa: E402
from app.models.user import User  # noqa: E402
from app.repositories.user_repo import UserRepository  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402
from app.services.durable_tool_runtime import DurableToolRunService  # noqa: E402


BASE_URL = os.getenv("AIWS_E2E_BASE_URL", "http://127.0.0.1:32018")
USER_EMAIL = os.getenv("AIWS_E2E_USER_EMAIL", "1528713326@qq.com").lower()


def prepare() -> tuple[str, str, str, str, str]:
    with SessionLocal() as db:
        pending = db.scalar(select(func.count()).select_from(AgentOutboxEvent).where(
            AgentOutboxEvent.status.in_(["pending", "running"])
        ))
        if pending:
            raise RuntimeError("已有未完成 Durable 事件，烟测不会启动可能领取其他任务的 Worker。")
        user = db.scalar(select(User).where(User.email == USER_EMAIL))
        if not user:
            raise RuntimeError("固定测试用户不存在。")
        token = AuthService(UserRepository(db)).create_access_token(user)
        project = Project(user_id=user.id, name=f"stage49-smoke-{uuid4().hex[:8]}")
        db.add(project)
        db.flush()
        conversation = Conversation(user_id=user.id, project_id=project.id, title="阶段 4.9 临时任务", model_name="smoke")
        db.add(conversation)
        db.flush()
        assistant = Message(conversation_id=conversation.id, role="assistant", content="临时只读任务", status="done")
        db.add(assistant)
        db.flush()
        run = DurableToolRunService(db).enqueue(
            user_id=user.id, project_id=project.id, conversation_id=conversation.id,
            assistant_message_id=assistant.id,
            calls=[{"call_id": "list-files", "tool_key": "workspace.files.list", "arguments": {}}],
        )
        return token, project.id, conversation.id, run.id, user.id


def cleanup(project_id: str, conversation_id: str, run_id: str, worker_owner: str) -> None:
    with SessionLocal() as db:
        db.execute(delete(AgentArtifact).where(AgentArtifact.run_id == run_id))
        db.execute(delete(AgentOutboxEvent).where(AgentOutboxEvent.run_id == run_id))
        db.execute(delete(AgentCheckpoint).where(AgentCheckpoint.run_id == run_id))
        db.execute(delete(AgentStep).where(AgentStep.run_id == run_id))
        db.execute(delete(AgentRun).where(AgentRun.id == run_id))
        db.execute(delete(Message).where(Message.conversation_id == conversation_id))
        db.execute(delete(Conversation).where(Conversation.id == conversation_id))
        db.execute(delete(Project).where(Project.id == project_id))
        db.execute(delete(AgentWorkerHeartbeat).where(AgentWorkerHeartbeat.owner == worker_owner))
        db.commit()


def main() -> None:
    token, project_id, conversation_id, run_id, user_id = prepare()
    owner = f"stage49-worker-{uuid4().hex[:10]}"
    worker: subprocess.Popen[bytes] | None = None
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1280, "height": 800})
            context.add_cookies([{"name": "aiws_token", "value": token, "url": BASE_URL, "httpOnly": True, "sameSite": "Lax"}])
            page = context.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
            page.goto(f"{BASE_URL}/tasks", wait_until="networkidle")
            expect(page.get_by_role("heading", name="后台任务")).to_be_visible()
            run_button = page.locator("section[aria-label='任务列表'] button").filter(has_text=run_id)
            assert run_button.count() == 1
            run_button.click()
            expect(page.get_by_text("workspace.files.list")).to_be_visible(timeout=10000)

            env = os.environ.copy()
            env["AGENT_WORKER_ID"] = owner
            env["AGENT_WORKER_PYTHON"] = sys.executable
            worker = subprocess.Popen(
                ["bash", "scripts/run_agent_tool_worker.sh"], cwd=ROOT / "backend", env=env,
                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            )
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                with SessionLocal() as db:
                    run = db.get(AgentRun, run_id)
                    heartbeat = db.get(AgentWorkerHeartbeat, owner)
                    if run and run.status == "succeeded" and heartbeat:
                        break
                if worker.poll() is not None:
                    detail = worker.communicate(timeout=2)[1].decode("utf-8", errors="replace")
                    raise AssertionError(f"Worker 在任务完成前退出：{detail[-1600:]}")
                time.sleep(0.2)
            else:
                raise AssertionError("Worker 未在 30 秒内完成只读任务。")

            page.get_by_role("button", name="刷新", exact=True).click()
            expect(page.get_by_role("heading", name=re.compile("任务详情 · 已完成"))).to_be_visible(timeout=10000)
            expect(page.get_by_role("heading", name="产物")).to_be_visible()
            # 4.11 审计下载只返回脱敏 JSONL，浏览器侧验证下载入口和响应路径。
            with page.expect_download(timeout=10000) as download_info:
                page.get_by_role("button", name="导出审计 JSONL", exact=True).click()
            download = download_info.value
            assert download.suggested_filename == f"agent-run-{run_id}.jsonl"
            page.screenshot(path="/tmp/aiws-stage49-desktop.png", full_page=True)
            mobile = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=1)
            mobile.add_cookies([{"name": "aiws_token", "value": token, "url": BASE_URL, "httpOnly": True, "sameSite": "Lax"}])
            mobile_page = mobile.new_page()
            mobile_page.on("pageerror", lambda error: errors.append(str(error)))
            mobile_page.goto(f"{BASE_URL}/tasks", wait_until="networkidle")
            expect(mobile_page.get_by_role("heading", name="后台任务")).to_be_visible()
            mobile_page.locator("section[aria-label='任务列表'] button").filter(has_text=run_id).click()
            expect(mobile_page.get_by_role("heading", name=re.compile("任务详情 · 已完成"))).to_be_visible(timeout=10000)
            mobile_page.screenshot(path="/tmp/aiws-stage49-mobile.png", full_page=True)
            assert mobile_page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
            mobile.close()
            page.get_by_role("link", name="查看原会话").click()
            page.wait_for_url(re.compile(r"/chat\?conversation="))
            page.wait_for_load_state("networkidle")
            expect(page.get_by_text("后台任务已完成", exact=False).first).to_be_visible(timeout=10000)
            page.get_by_text("来源", exact=False).first.click()
            detail_link = page.get_by_role("link", name="查看任务详情")
            expect(detail_link).to_be_visible(timeout=10000)
            detail_link.click()
            page.wait_for_url(re.compile(r"/tasks\?run="))
            expect(page.get_by_role("heading", name=re.compile("任务详情 · 已完成"))).to_be_visible(timeout=10000)
            assert not errors, errors
            browser.close()

        worker.terminate()
        worker.wait(timeout=10)
        with SessionLocal() as db:
            run = db.get(AgentRun, run_id)
            heartbeat = db.get(AgentWorkerHeartbeat, owner)
            projected = db.scalars(select(Message).where(
                Message.conversation_id == conversation_id, Message.content.contains("后台任务已完成")
            )).all()
            assert run and run.status == "succeeded"
            assert heartbeat and heartbeat.status == "stopped"
            assert len(projected) == 1
            assert db.scalar(select(func.count()).select_from(AgentArtifact).where(AgentArtifact.run_id == run_id)) == 1
        print("stage49 real Worker + browser + conversation projection: OK")
    finally:
        if worker and worker.poll() is None:
            worker.terminate()
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait(timeout=5)
        cleanup(project_id, conversation_id, run_id, owner)


if __name__ == "__main__":
    main()
