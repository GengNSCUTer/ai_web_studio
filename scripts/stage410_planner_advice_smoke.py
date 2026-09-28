"""阶段 4.10：浏览器验证建议、预览、确认与持久化边界。"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from uuid import uuid4

from playwright.sync_api import expect, sync_playwright
from sqlalchemy import delete, func, select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.database import SessionLocal  # noqa: E402
from app.models.agent_runtime import AgentArtifact, AgentCheckpoint, AgentOutboxEvent, AgentRun, AgentStep  # noqa: E402
from app.models.conversation import Conversation  # noqa: E402
from app.models.message import Message  # noqa: E402
from app.models.project import Project  # noqa: E402
from app.models.project_file import ProjectFile  # noqa: E402
from app.models.tool_trace import ToolCallRun, ToolRouteRun  # noqa: E402
from app.models.user import User  # noqa: E402
from app.repositories.tool_trace_repo import ToolTraceRepository  # noqa: E402
from app.repositories.user_repo import UserRepository  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402
from app.services.tools.schemas import ExternalContextResult, PlannedToolCall, ToolPlan, ToolTraceEvent  # noqa: E402


BASE_URL = os.getenv("AIWS_E2E_BASE_URL", "http://127.0.0.1:32018").rstrip("/")
USER_EMAIL = os.getenv("AIWS_E2E_USER_EMAIL", "1528713326@qq.com").lower()


def prepare() -> tuple[str, str, str, str]:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == USER_EMAIL))
        if not user:
            raise RuntimeError("固定测试用户不存在。")
        project = Project(user_id=user.id, name=f"stage410-smoke-{uuid4().hex[:8]}")
        db.add(project)
        db.flush()
        project_file = ProjectFile(
            user_id=user.id, project_id=project.id, kind="text", mime_type="text/markdown",
            file_name="stage410-readme.md", file_size=25,
            storage_key=f"stage410/{uuid4().hex}/readme.md", parsed_text="# 阶段 4.10 只读测试",
        )
        db.add(project_file)
        conversation = Conversation(user_id=user.id, project_id=project.id, title="阶段 4.10 临时会话", model_name="smoke")
        db.add(conversation)
        db.flush()
        user_message = Message(conversation_id=conversation.id, sequence=1, role="user", content="列出并查找项目文件", status="done")
        assistant = Message(conversation_id=conversation.id, sequence=2, role="assistant", content="已完成同步查询。", status="done")
        db.add_all([user_message, assistant])
        db.flush()
        calls = [
            PlannedToolCall(
                call_id="list-files", tool_key="workspace.files.list", provider="workspace", category="workspace_file",
                display_name="列出项目文件", confidence=0.9, reason="列出文件", arguments={},
            ),
            PlannedToolCall(
                call_id="search-files", tool_key="workspace.files.search", provider="workspace", category="workspace_file",
                display_name="搜索项目文件", confidence=0.9, reason="查找文件", arguments={"query": "stage410"},
            ),
        ]
        plan = ToolPlan(
            plan_id=str(uuid4()), router="llm_tool_planner_v1", external_context_allowed=True,
            should_use_tools=True, calls=calls, execution_mode="durable_candidate",
            execution_reason="包含两个低风险只读步骤。",
        )
        event = ToolTraceEvent(type="tool_plan", payload={"round": 1, "plan": plan.to_public_dict()})
        ToolTraceRepository(db).replace_for_assistant_message(
            user_id=user.id, conversation_id=conversation.id, user_message_id=user_message.id,
            assistant_message_id=assistant.id, query=user_message.content,
            external_context=ExternalContextResult(
                context_text=None, sources=[], notices=[], diagnostics={}, details={},
                tool_plan=plan, tool_events=[event],
            ),
        )
        return AuthService(UserRepository(db)).create_access_token(user), project.id, conversation.id, assistant.id


def run_count(assistant_message_id: str) -> int:
    with SessionLocal() as db:
        return int(db.scalar(select(func.count()).select_from(AgentRun).where(
            AgentRun.assistant_message_id == assistant_message_id
        )) or 0)


def cleanup(project_id: str, conversation_id: str, assistant_message_id: str) -> None:
    with SessionLocal() as db:
        run_ids = list(db.scalars(select(AgentRun.id).where(AgentRun.assistant_message_id == assistant_message_id)))
        if run_ids:
            db.execute(delete(AgentArtifact).where(AgentArtifact.run_id.in_(run_ids)))
            db.execute(delete(AgentOutboxEvent).where(AgentOutboxEvent.run_id.in_(run_ids)))
            db.execute(delete(AgentCheckpoint).where(AgentCheckpoint.run_id.in_(run_ids)))
            db.execute(delete(AgentStep).where(AgentStep.run_id.in_(run_ids)))
            db.execute(delete(AgentRun).where(AgentRun.id.in_(run_ids)))
        route_ids = list(db.scalars(select(ToolRouteRun.id).where(ToolRouteRun.conversation_id == conversation_id)))
        if route_ids:
            db.execute(delete(ToolCallRun).where(ToolCallRun.route_run_id.in_(route_ids)))
            db.execute(delete(ToolRouteRun).where(ToolRouteRun.id.in_(route_ids)))
        db.execute(delete(Message).where(Message.conversation_id == conversation_id))
        db.execute(delete(ProjectFile).where(ProjectFile.project_id == project_id))
        db.execute(delete(Conversation).where(Conversation.id == conversation_id))
        db.execute(delete(Project).where(Project.id == project_id))
        db.commit()


def main() -> None:
    token, project_id, conversation_id, assistant_message_id = prepare()
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1280, "height": 800})
            context.add_cookies([{"name": "aiws_token", "value": token, "url": BASE_URL, "httpOnly": True, "sameSite": "Lax"}])
            page = context.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
            page.goto(f"{BASE_URL}/chat?conversation={conversation_id}", wait_until="networkidle")
            page.locator("details.reasoning-panel summary").first.click()
            assert page.locator("details.reasoning-panel").first.evaluate("element => element.open")
            expect(page.get_by_text("Planner 建议：这组步骤适合可恢复任务。", exact=False)).to_be_visible()
            expect(page.get_by_text("可能重复本轮查询。", exact=False)).to_be_visible()
            assert run_count(assistant_message_id) == 0
            page.get_by_role("button", name="预览后台重执行").click()
            expect(page.get_by_text("将执行的只读步骤")).to_be_visible(timeout=10000)
            assert run_count(assistant_message_id) == 0
            page.get_by_role("button", name="确认并后台执行").click()
            expect(page.get_by_text("Durable Run：", exact=False)).to_be_visible(timeout=10000)
            assert run_count(assistant_message_id) == 1
            page.screenshot(path="/tmp/aiws-stage410-desktop.png", full_page=True)
            mobile = browser.new_context(viewport={"width": 390, "height": 844})
            mobile.add_cookies([{"name": "aiws_token", "value": token, "url": BASE_URL, "httpOnly": True, "sameSite": "Lax"}])
            mobile_page = mobile.new_page()
            mobile_page.on("pageerror", lambda error: errors.append(str(error)))
            mobile_page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
            mobile_page.goto(f"{BASE_URL}/chat?conversation={conversation_id}", wait_until="networkidle")
            mobile_page.locator("details.reasoning-panel summary").first.click()
            expect(mobile_page.get_by_text("Planner 建议：这组步骤适合可恢复任务。", exact=False)).to_be_visible()
            assert mobile_page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
            mobile_page.screenshot(path="/tmp/aiws-stage410-mobile.png", full_page=True)
            browser.close()
            assert not errors, errors
        print("stage410 planner advice + preview + confirm browser smoke: OK")
    finally:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            with SessionLocal() as db:
                run = db.scalar(select(AgentRun).where(AgentRun.assistant_message_id == assistant_message_id))
                if not run or run.status not in {"queued", "running"}:
                    break
            time.sleep(0.2)
        cleanup(project_id, conversation_id, assistant_message_id)


if __name__ == "__main__":
    main()
