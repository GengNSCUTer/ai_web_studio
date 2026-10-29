"""固定账号真实浏览器验收；验证安装授权与不可用阻断，不冒充容器执行。"""

import json
import os
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from playwright.sync_api import sync_playwright, expect
from sqlalchemy import select, delete
from app.core.database import SessionLocal
from app.models.user import User
from app.models.project import Project
from app.models.project_file import ProjectFile
from app.models.plugin import PluginInstallation, PluginRelease, ProjectPlugin, SandboxJob, PluginSubmission
from app.models.tool_config import UserSkillInstallation, SkillInstallationRevision
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.tool_trace import ToolCallRun, ToolRouteRun
from app.models.user_setting import UserSetting
from app.repositories.user_repo import UserRepository
from app.services.auth_service import AuthService


def run():
    key = "knowledge.organize"
    suffix = uuid4().hex[:8]
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "1528713326@qq.com"))
        if not user:
            raise RuntimeError("固定账号不存在")
        uid = user.id
        token = AuthService(UserRepository(db)).create_access_token(user)
        old_installation = db.scalar(select(PluginInstallation).where(PluginInstallation.user_id == uid, PluginInstallation.plugin_key == key))
        old_installation_values = {c.name: getattr(old_installation, c.name) for c in PluginInstallation.__table__.columns} if old_installation else None
        old_releases = set(db.scalars(select(PluginRelease.id).where(PluginRelease.user_id == uid, PluginRelease.plugin_key == key)))
        skill = db.scalar(select(UserSkillInstallation).where(UserSkillInstallation.user_id == uid, UserSkillInstallation.skill_key == "workspace.document-review"))
        old_skill = {c.name: getattr(skill, c.name) for c in UserSkillInstallation.__table__.columns} if skill else None
        old_skill_revisions = set(db.scalars(select(SkillInstallationRevision.id).where(SkillInstallationRevision.user_id == uid, SkillInstallationRevision.skill_key == "workspace.document-review")))
        project = Project(user_id=uid, name=f"plugin-smoke-{suffix}")
        db.add(project); db.flush()
        pid = project.id
        db.add(ProjectFile(project_id=pid, user_id=uid, kind="text", file_name="插件测试资料.md",
                           parsed_text="# 测试资料\n仅本次授权副本；原文件不应被修改。", storage_key=f"{uid}/test-only-{suffix}"))
        db.commit()
    errors = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            context.add_cookies([{"name": "aiws_token", "value": token, "url": "http://127.0.0.1:32008", "httpOnly": True, "sameSite": "Lax"}])
            page = context.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda msg: errors.append(msg.text) if msg.type == "error" else None)
            page.goto("http://127.0.0.1:32008/plugins", wait_until="networkidle")
            expect(page.get_by_role("heading", name="审核插件中心", exact=True)).to_be_visible()
            expect(page.locator("article")).to_have_count(4)
            page.get_by_label("当前工作区").select_option(pid)
            card = page.locator("article").filter(has=page.get_by_role("heading", name="知识整理", exact=True))
            card.get_by_role("button", name="安装预检", exact=True).click()
            expect(page.get_by_role("button", name="确认安装审核版本")).to_be_visible()
            page.get_by_role("button", name="确认安装审核版本").click()
            expect(card.get_by_role("button", name="在项目启用")).to_be_enabled()
            card.get_by_role("button", name="在项目启用").click()
            expect(card.get_by_role("button", name="停用项目")).to_be_visible()
            page.get_by_label("插件测试资料.md", exact=True).check()
            page.get_by_role("button", name="预览本次脚本授权", exact=True).click()
            expect(page.get_by_text("任务状态：awaiting_approval", exact=True)).to_be_visible()
            expect(page.get_by_role("button", name="批准本次隔离执行")).to_be_disabled()
            expect(page.get_by_text("授权副本：插件测试资料.md", exact=True)).to_be_visible()
            page.screenshot(path="/tmp/aiws-plugin-center-desktop.png", full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            page.screenshot(path="/tmp/aiws-plugin-center-mobile.png", full_page=True)
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1"), "移动端横向溢出"
            # 即使绕过禁用按钮直接请求，服务端仍拒绝，不创建执行队列。
            with SessionLocal() as db:
                job = db.scalar(select(SandboxJob).where(SandboxJob.project_id == pid))
                job_id = job.id
            response = context.request.post(f"http://127.0.0.1:32008/api/backend/plugins/sandbox/{job_id}/confirm", data={"approved": True})
            assert response.status == 400, response.status
            with SessionLocal() as db:
                assert db.get(SandboxJob, job_id).status == "awaiting_approval"
            online_chat = "not_requested"
            if os.getenv("AIWS_PLUGIN_CHAT_SMOKE") == "1":
                with SessionLocal() as db:
                    setting = db.scalar(select(UserSetting).where(UserSetting.user_id == uid))
                    assert setting.provider_type == "openai-compatible", "只使用账号在线 API，不加载本地模型"
                page.goto("http://127.0.0.1:32008/chat", wait_until="networkidle")
                page.locator("select").nth(0).select_option(pid)
                page.get_by_role("button", name="新对话", exact=True).click()
                page.get_by_test_id("tool-run-mode-selector").select_option("workspace_review")
                page.get_by_test_id("skill-selector").click()
                page.get_by_test_id("skill-option-workspace.document-review").click()
                page.get_by_placeholder("输入你的问题").fill("请搜索并读取当前工作区的 插件测试资料.md，只逐字引用前两行；不要联网、不要修改。")
                page.get_by_role("button", name="发送消息", exact=True).click()
                page.wait_for_function("() => document.querySelector('button[type=submit]')?.disabled === true", timeout=10000)
                page.wait_for_function("() => ['发送消息','Send'].includes(document.querySelector('button[type=submit]')?.textContent?.trim())", timeout=240000)
                with SessionLocal() as db:
                    conversation = db.scalar(select(Conversation).where(Conversation.project_id == pid))
                    answer = db.scalar(select(Message).where(Message.conversation_id == conversation.id, Message.role == "assistant").order_by(Message.created_at.desc()))
                    assert answer.status == "done" and "原文件不应被修改" in answer.content, "模型最终回答未正确使用插件文件证据"
                    route_ids = list(db.scalars(select(ToolRouteRun.id).where(ToolRouteRun.conversation_id == conversation.id)))
                    executed = list(db.scalars(select(ToolCallRun.tool_key).where(ToolCallRun.route_run_id.in_(route_ids))))
                    assert "workspace.files.read" in executed, "未真实调用文件读取工具"
                online_chat = "passed_planner_read_answer_persistence"
            assert not errors, errors
            context.close(); browser.close()
        print(json.dumps({"cards": 4, "install": "passed", "project_enable": "passed", "preview": "passed",
                          "missing_runtime_refusal": "passed", "mobile_overflow": False, "browser_errors": 0,
                          "online_chat": online_chat,
                          "real_container_execution": "not_run_missing_rootless_prerequisites"}, ensure_ascii=False))
    finally:
        from stage46a_real_e2e import cleanup as cleanup_chat
        cleanup_chat(user_id=uid, project_id=pid)
        with SessionLocal() as db:
            db.execute(delete(SandboxJob).where(SandboxJob.project_id == pid))
            db.execute(delete(ProjectPlugin).where(ProjectPlugin.project_id == pid))
            db.execute(delete(ProjectFile).where(ProjectFile.project_id == pid))
            db.execute(delete(Project).where(Project.id == pid))
            current = db.scalar(select(PluginInstallation).where(PluginInstallation.user_id == uid, PluginInstallation.plugin_key == key))
            if old_installation_values:
                for name, value in old_installation_values.items(): setattr(current, name, value)
            elif current:
                db.delete(current)
            for release in db.scalars(select(PluginRelease).where(PluginRelease.user_id == uid, PluginRelease.plugin_key == key)):
                if release.id not in old_releases: db.delete(release)
            current_skill = db.scalar(select(UserSkillInstallation).where(UserSkillInstallation.user_id == uid, UserSkillInstallation.skill_key == "workspace.document-review"))
            if old_skill:
                for name, value in old_skill.items(): setattr(current_skill, name, value)
            elif current_skill:
                db.delete(current_skill)
            for revision in db.scalars(select(SkillInstallationRevision).where(SkillInstallationRevision.user_id == uid, SkillInstallationRevision.skill_key == "workspace.document-review")):
                if revision.id not in old_skill_revisions: db.delete(revision)
            db.commit()


if __name__ == "__main__":
    run()
