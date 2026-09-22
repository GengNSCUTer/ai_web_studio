"""阶段 4.6A 真实在线模型与浏览器端到端验证。

测试只创建临时项目和脱敏文本文件，验证工作区文件读取，不执行写入。
在线模型配置从测试账号已有设置读取，脚本不会输出 API Key 或响应中的敏感字段。
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
MODEL_TIMEOUT_MS = int(os.getenv("AIWS_E2E_TIMEOUT_MS", "240000"))


def prepare_fixture() -> tuple[str, str, str, str, str]:
    """创建临时项目和文件，返回 token、用户、项目、文件和项目名。"""
    with SessionLocal() as db:
        user = db.scalars(select(User).where(User.email == USER_EMAIL).limit(1)).first()
        if not user:
            raise RuntimeError("固定 E2E 测试账号不存在。")
        setting = db.scalars(select(UserSetting).where(UserSetting.user_id == user.id).limit(1)).first()
        if not setting or setting.provider_type not in {"openai-compatible", "vllm"}:
            raise RuntimeError("测试账号未配置在线 openai-compatible/vLLM Provider。")
        if not (setting.default_model or "").strip():
            raise RuntimeError("测试账号没有可用默认模型。")

        suffix = uuid.uuid4().hex[:10]
        project_name = f"stage46a-real-e2e-{suffix}"
        project = Project(
            user_id=user.id,
            name=project_name,
            description="阶段 4.6A 临时真实端到端测试项目",
            default_model=setting.default_model,
        )
        db.add(project)
        db.flush()
        project_file = ProjectFile(
            project_id=project.id,
            user_id=user.id,
            kind="text",
            mime_type="text/markdown",
            file_name="stage46a-readme.md",
            file_size=110,
            storage_key=f"stage46a/{suffix}/readme.md",
            parsed_text=(
                "# 阶段 4.6A 临时文件\n"
                "用途：验证工作区文件工具的真实读取和最终回答链路。\n"
                "安全边界：本次测试只读，不允许修改文件。\n"
            ),
        )
        db.add(project_file)
        db.commit()
        token = AuthService(UserRepository(db)).create_access_token(user)
        return token, user.id, project.id, project_file.id, project_name


def collect_result(*, user_id: str, project_id: str, file_id: str) -> dict:
    """读取并校验真实 Chat 产生的 Planner、Tool 和最终回答结果。"""
    with SessionLocal() as db:
        conversation = db.scalars(
            select(Conversation)
            .where(Conversation.user_id == user_id, Conversation.project_id == project_id)
            .order_by(Conversation.created_at.desc())
            .limit(1)
        ).first()
        if not conversation:
            raise AssertionError("没有找到真实浏览器创建的测试会话。")
        route_run = db.scalars(
            select(ToolRouteRun)
            .where(ToolRouteRun.user_id == user_id, ToolRouteRun.conversation_id == conversation.id)
            .order_by(ToolRouteRun.created_at.desc())
            .limit(1)
        ).first()
        if not route_run:
            raise AssertionError("没有找到真实 ToolRouteRun。")
        plan = json.loads(route_run.plan_json or "{}")
        events = json.loads(route_run.events_json or "[]")
        plan_calls = plan.get("calls") or []
        planner_end = next(
            (event for event in events if event.get("type") == "tool_planner_end"),
            {},
        )
        if planner_end.get("strategy") != "llm_primary":
            raise AssertionError(f"真实在线模型未完成 LLM Planner：{planner_end.get('strategy')!r}")
        planned_tool_keys = {str(call.get("tool_key")) for call in plan_calls if isinstance(call, dict)}
        tool_calls = list(
            db.scalars(select(ToolCallRun).where(ToolCallRun.route_run_id == route_run.id)).all()
        )
        tool_keys = {str(call.tool_key) for call in tool_calls}
        required_tool_keys = {"workspace.files.search", "workspace.files.read"}
        if not required_tool_keys.issubset(tool_keys):
            candidate_event = next(
                (event for event in events if event.get("type") == "tool_candidate_selection"),
                {},
            )
            candidate_keys = [
                item.get("tool_key")
                for item in candidate_event.get("candidates", [])
                if isinstance(item, dict)
            ]
            raise AssertionError(
                f"真实 Planner/Workflow 没有完成 search + read：{sorted(tool_keys)}；"
                f"候选工具为：{candidate_keys}"
            )

        assistant = db.scalars(
            select(Message)
            .where(Message.id == route_run.assistant_message_id)
            .limit(1)
        ).first()
        if not assistant or assistant.status != "done" or not (assistant.content or "").strip():
            raise AssertionError("真实最终回答没有成功持久化为 done。")
        if "stage46a-readme.md" not in assistant.content:
            raise AssertionError("最终回答没有引用临时工作区文件名。")
        if "# 阶段 4.6A 临时文件" not in assistant.content:
            raise AssertionError("最终回答没有忠实引用文件第一行，可能存在工具结果不足时的模型猜测。")

        file_row = db.get(ProjectFile, file_id)
        if not file_row or "验证工作区文件工具" not in (file_row.parsed_text or ""):
            raise AssertionError("测试文件正文发生了非预期修改。")
        return {
            "conversation_id": conversation.id,
            "route_run_id": route_run.id,
            "planner_strategy": planner_end.get("strategy"),
            "last_plan_tool_keys": sorted(planned_tool_keys),
            "executed_tool_keys": sorted(tool_keys),
            "tool_call_count": len(tool_calls),
            "assistant_status": assistant.status,
            "assistant_chars": len(assistant.content or ""),
            "route_status": route_run.status,
        }


def cleanup(*, user_id: str, project_id: str) -> None:
    """删除本次临时会话及项目，避免污染固定测试账号。"""
    with SessionLocal() as db:
        conversations = list(
            db.scalars(
                select(Conversation).where(
                    Conversation.user_id == user_id,
                    Conversation.project_id == project_id,
                )
            ).all()
        )
        conversation_ids = [item.id for item in conversations]
        if conversation_ids:
            route_ids = list(
                db.scalars(select(ToolRouteRun.id).where(ToolRouteRun.conversation_id.in_(conversation_ids))).all()
            )
            if route_ids:
                db.execute(delete(ToolCallRun).where(ToolCallRun.route_run_id.in_(route_ids)))
                db.execute(delete(ToolRouteRun).where(ToolRouteRun.id.in_(route_ids)))
            for conversation in conversations:
                db.delete(conversation)
        project = db.get(Project, project_id)
        if project:
            db.delete(project)
        db.commit()


def run() -> None:
    token, user_id, project_id, file_id, project_name = prepare_fixture()
    console_errors: list[str] = []
    captured_chat: dict = {}
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            context.add_cookies(
                [{"name": "aiws_token", "value": token, "url": BASE_URL, "httpOnly": True, "sameSite": "Lax"}]
            )
            page = context.new_page()
            page.on("pageerror", lambda error: console_errors.append(f"pageerror: {error}"))
            page.on(
                "console",
                lambda message: console_errors.append(f"console: {message.text}")
                if message.type == "error"
                else None,
            )

            def capture_request(request) -> None:
                if request.method == "POST" and request.url.rstrip("/").endswith("/api/chat"):
                    try:
                        captured_chat.update(request.post_data_json or {})
                    except Exception:
                        pass

            page.on("request", capture_request)
            page.goto(f"{BASE_URL}/chat", wait_until="networkidle", timeout=60_000)
            expect(page.get_by_placeholder("输入你的问题")).to_be_visible(timeout=30_000)

            # 测试账号中的内置 Skill 可能是旧版本锁；按产品真实升级 API 更新到当前已审核版本。
            upgrade = page.evaluate(
                """async () => {
                    const response = await fetch('/api/backend/tools/skills/workspace.document-review/upgrade', {method: 'POST'});
                    return {status: response.status, body: await response.json().catch(() => null)};
                }"""
            )
            if upgrade.get("status") != 200:
                raise AssertionError(f"工作区审阅 Skill 升级失败：{upgrade.get('status')}")
            page.reload(wait_until="networkidle")
            expect(page.get_by_placeholder("输入你的问题")).to_be_visible(timeout=30_000)

            workspace_select = page.locator("select").nth(0)
            if project_id not in workspace_select.locator("option").evaluate_all("options => options.map(o => o.value)"):
                raise AssertionError("前端没有加载临时测试工作区。")
            workspace_select.select_option(project_id)
            page.get_by_role("button", name="新对话").click()
            page.get_by_test_id("tool-run-mode-selector").select_option("workspace_review")
            # 显式激活已审核的只读工作区 Skill，让候选集只包含文件 list/search/read。
            page.get_by_test_id("skill-selector").click()
            page.get_by_test_id("skill-option-workspace.document-review").wait_for(timeout=30_000)
            page.get_by_test_id("skill-option-workspace.document-review").click()
            composer = page.get_by_placeholder("输入你的问题")
            composer.fill(
                "在当前工作区查找并读取 stage46a-readme.md，告诉我文件用途和第一行标题。"
                "只使用工作区文件工具，先搜索再读取，不要联网，也不要修改文件。"
            )
            page.get_by_role("button", name="发送消息").click()
            page.wait_for_function(
                "() => document.querySelector('button[type=submit]')?.disabled === true",
                timeout=10_000,
            )
            page.wait_for_function(
                """() => {
                    const button = document.querySelector('button[type=submit]');
                    const text = button?.textContent?.trim() || '';
                    return text === '发送消息' || text === 'Send';
                }""",
                timeout=MODEL_TIMEOUT_MS,
            )
            tool_summary = page.get_by_text(re.compile(r"工具过程 · \d+"))
            expect(tool_summary).to_be_visible(timeout=10_000)
            tool_summary.click()
            expect(page.get_by_text(re.compile("工作区文件.*调用完成")).first).to_be_visible(timeout=10_000)
            page.screenshot(path="/tmp/stage46a-real-e2e.png", full_page=True)
            browser.close()

        if captured_chat.get("toolRunMode") != "workspace_review":
            raise AssertionError("浏览器请求没有携带 workspace_review 运行模式。")
        if console_errors:
            raise AssertionError("浏览器出现错误：\n" + "\n".join(console_errors[:5]))
        result = collect_result(user_id=user_id, project_id=project_id, file_id=file_id)
        result["project_name"] = project_name
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    finally:
        if os.getenv("AIWS_E2E_KEEP_FIXTURE") != "1":
            cleanup(user_id=user_id, project_id=project_id)


if __name__ == "__main__":
    run()
