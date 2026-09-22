"""阶段 4.6B 真实在线模型文件修改提案端到端验证。

仅创建临时项目和 Markdown 文件。在线模型只能生成 Diff；实际写入仍由浏览器
模拟真实用户点击确认后，经 challenge、approval token 和 Revision CAS 完成。
脚本不输出账号密码、API Key 或原始 Provider 响应。
"""

from __future__ import annotations

import json
import os
import re
import sys
import uuid
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from sqlalchemy import delete, select


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.database import SessionLocal  # noqa: E402
from app.models.agent_runtime import AgentApproval, FileRevision  # noqa: E402
from app.models.conversation import Conversation  # noqa: E402
from app.models.message import Message  # noqa: E402
from app.models.project import Project  # noqa: E402
from app.models.project_file import ProjectFile  # noqa: E402
from app.models.tool_trace import ToolCallRun, ToolRouteRun  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.user_setting import UserSetting  # noqa: E402
from app.repositories.user_repo import UserRepository  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402


BASE_URL = os.getenv("AIWS_E2E_BASE_URL", "http://127.0.0.1:32008").rstrip("/")
USER_EMAIL = os.getenv("AIWS_E2E_USER_EMAIL", "1528713326@qq.com").strip().lower()
MODEL_TIMEOUT_MS = int(os.getenv("AIWS_E2E_TIMEOUT_MS", "300000"))


def prepare_fixture() -> tuple[str, str, str, str, str]:
    """创建只用于本次修改验证的临时工作区和唯一替换文本。"""

    with SessionLocal() as db:
        user = db.scalars(select(User).where(User.email == USER_EMAIL).limit(1)).first()
        setting = db.scalars(select(UserSetting).where(UserSetting.user_id == user.id).limit(1)).first() if user else None
        if not user or not setting or setting.provider_type not in {"openai-compatible", "vllm"}:
            raise RuntimeError("固定测试账号缺少在线模型配置。")
        suffix = uuid.uuid4().hex[:10]
        project = Project(
            user_id=user.id,
            name=f"stage46b-real-edit-{suffix}",
            description="阶段 4.6B 临时真实编辑端到端测试项目",
            default_model=setting.default_model,
        )
        db.add(project)
        db.flush()
        project_file = ProjectFile(
            project_id=project.id,
            user_id=user.id,
            kind="text",
            mime_type="text/markdown",
            file_name="stage46b-edit.md",
            file_size=160,
            storage_key=f"stage46b/{suffix}/edit.md",
            parsed_text="# 阶段4.6B旧标题\n用途：验证真实模型只能提出修改，用户确认后才通过 CAS 写入。\n",
        )
        db.add(project_file)
        db.commit()
        return AuthService(UserRepository(db)).create_access_token(user), user.id, project.id, project_file.id, project.name


def cleanup(*, user_id: str, project_id: str) -> None:
    """删除临时项目及所有关联会话和工具追踪。"""

    with SessionLocal() as db:
        conversations = list(db.scalars(select(Conversation).where(Conversation.user_id == user_id, Conversation.project_id == project_id)).all())
        ids = [item.id for item in conversations]
        if ids:
            route_ids = list(db.scalars(select(ToolRouteRun.id).where(ToolRouteRun.conversation_id.in_(ids))).all())
            if route_ids:
                db.execute(delete(ToolCallRun).where(ToolCallRun.route_run_id.in_(route_ids)))
                db.execute(delete(ToolRouteRun).where(ToolRouteRun.id.in_(route_ids)))
            for item in conversations:
                db.delete(item)
        project = db.get(Project, project_id)
        if project:
            db.delete(project)
        db.commit()


def install_skill(page, skill_key: str) -> None:
    """使用产品真实安装 API 显式启用审核过的内置 Skill。"""

    result = page.evaluate(
        """async ({ skillKey }) => {
            const response = await fetch(`/api/backend/tools/skills/${encodeURIComponent(skillKey)}`, {
              method: 'PUT', headers: {'content-type': 'application/json'},
              body: JSON.stringify({is_enabled: true}),
            });
            return {status: response.status, body: await response.json().catch(() => null)};
        }""",
        {"skillKey": skill_key},
    )
    if result.get("status") != 200:
        raise AssertionError(f"Skill 安装或启用失败：{skill_key} / {result.get('status')}")


def wait_until_sent(page) -> None:
    page.wait_for_function(
        """() => {
            const button = document.querySelector('button[type=submit]');
            const text = button?.textContent?.trim() || '';
            return text === '发送消息' || text === 'Send';
        }""",
        timeout=MODEL_TIMEOUT_MS,
    )


def collect_edit_result(*, user_id: str, project_id: str, file_id: str) -> dict:
    """验证真实 Planner、审批状态、CAS Revision 与读取会话均已落库。"""

    with SessionLocal() as db:
        routes = list(
            db.scalars(
                select(ToolRouteRun)
                .where(ToolRouteRun.user_id == user_id)
                .order_by(ToolRouteRun.created_at.asc())
            ).all()
        )
        edit_route = next((item for item in routes if "阶段4.6B旧标题" in (item.query or "")), None)
        if not edit_route:
            raise AssertionError("没有找到真实文件修改提案的 ToolRouteRun。")
        events = json.loads(edit_route.events_json or "[]")
        planner_end = next((item for item in events if item.get("type") == "tool_planner_end"), {})
        if planner_end.get("strategy") != "llm_primary":
            raise AssertionError("文件修改没有使用真实在线 LLM Planner。")
        executed = {
            item.tool_key
            for item in db.scalars(select(ToolCallRun).where(ToolCallRun.route_run_id == edit_route.id)).all()
        }
        required = {"workspace.files.search", "workspace.files.read", "workspace.files.apply_edit"}
        if not required.issubset(executed):
            raise AssertionError(f"真实修改链路缺少 Tool：{sorted(executed)}")
        approval = db.scalars(
            select(AgentApproval).where(AgentApproval.file_id == file_id).order_by(AgentApproval.created_at.desc()).limit(1)
        ).first()
        if not approval or approval.status != "applied":
            raise AssertionError("用户确认后的审批没有收口为 applied。")
        revisions = list(db.scalars(select(FileRevision).where(FileRevision.file_id == file_id).order_by(FileRevision.revision_number.asc())).all())
        file_row = db.get(ProjectFile, file_id)
        if not file_row or "# 阶段4.6B新标题" not in (file_row.parsed_text or ""):
            raise AssertionError("CAS 应用后文件正文没有更新为新标题。")
        if len(revisions) != 2:
            raise AssertionError(f"预期初始版本和一次 CAS 写入共 2 个 Revision，实际为 {len(revisions)}。")
        read_conversation = db.scalars(
            select(Conversation)
            .where(Conversation.user_id == user_id, Conversation.project_id == project_id)
            .order_by(Conversation.created_at.desc())
            .limit(1)
        ).first()
        read_message = db.scalars(
            select(Message)
            .where(Message.conversation_id == read_conversation.id, Message.role == "assistant")
            .order_by(Message.created_at.desc())
            .limit(1)
        ).first()
        if not read_message or read_message.status != "done" or "阶段4.6B新标题" not in (read_message.content or ""):
            raise AssertionError("确认写入后，真实读取会话没有核验新版本标题。")
        return {
            "planner_strategy": planner_end.get("strategy"),
            "executed_tool_keys": sorted(executed),
            "approval_status": approval.status,
            "revision_count": len(revisions),
            "read_assistant_status": read_message.status,
        }


def run() -> None:
    token, user_id, project_id, file_id, project_name = prepare_fixture()
    console_errors: list[str] = []
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            context.add_cookies([{"name": "aiws_token", "value": token, "url": BASE_URL, "httpOnly": True, "sameSite": "Lax"}])
            page = context.new_page()
            page.on("pageerror", lambda error: console_errors.append(f"pageerror: {error}"))
            page.on("console", lambda message: console_errors.append(f"console: {message.text}") if message.type == "error" else None)
            page.goto(f"{BASE_URL}/chat", wait_until="networkidle", timeout=60_000)
            expect(page.get_by_placeholder("输入你的问题")).to_be_visible(timeout=30_000)
            install_skill(page, "workspace.document-edit")
            install_skill(page, "workspace.document-review")
            page.reload(wait_until="networkidle")

            workspace = page.locator("select").nth(0)
            workspace.select_option(project_id)
            page.get_by_role("button", name="新对话").click()
            page.get_by_test_id("tool-run-mode-selector").select_option("edit_proposal")
            page.get_by_test_id("skill-selector").click()
            page.get_by_test_id("skill-option-workspace.document-edit").click()
            composer = page.get_by_placeholder("输入你的问题")
            composer.fill("在当前工作区找到 stage46b-edit.md，读取后把“阶段4.6B旧标题”替换为“阶段4.6B新标题”。只生成 Diff，不要直接写入。")
            page.get_by_role("button", name="发送消息").click()
            wait_until_sent(page)
            expect(page.get_by_text(re.compile(r"工具过程 · \d+")).first).to_be_visible(timeout=20_000)
            page.get_by_text(re.compile(r"工具过程 · \d+")).first.click()
            expect(page.get_by_role("button", name="确认并应用 Diff")).to_be_visible(timeout=20_000)
            page.get_by_role("button", name="确认并应用 Diff").click()
            expect(page.get_by_text("已通过版本 CAS 应用修改")).to_be_visible(timeout=30_000)

            # 第二个真实会话只读新版本，验证不是仅相信 apply API 回包。
            page.get_by_role("button", name="新对话").click()
            page.get_by_test_id("tool-run-mode-selector").select_option("workspace_review")
            page.get_by_test_id("skill-selector").click()
            page.get_by_test_id("skill-option-workspace.document-review").click()
            composer.fill("读取 stage46b-edit.md 的第一行标题，核对刚才确认后的当前文件版本。")
            page.get_by_role("button", name="发送消息").click()
            wait_until_sent(page)
            expect(page.get_by_text("阶段4.6B新标题").last).to_be_visible(timeout=20_000)
            page.screenshot(path="/tmp/stage46b-real-edit-e2e.png", full_page=True)
            browser.close()
        if console_errors:
            raise AssertionError("浏览器出现错误：\n" + "\n".join(console_errors[:5]))
        result = collect_edit_result(user_id=user_id, project_id=project_id, file_id=file_id)
        result["project_name"] = project_name
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    finally:
        if os.getenv("AIWS_E2E_KEEP_FIXTURE") != "1":
            cleanup(user_id=user_id, project_id=project_id)


if __name__ == "__main__":
    run()
