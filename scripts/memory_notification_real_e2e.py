"""真实浏览器对话触发独立 Worker，验证即时通知、刷新去重、撤销和待审边界。"""

import json
import os
from pathlib import Path
import sys
import time
from uuid import uuid4

import httpx
from playwright.sync_api import expect, sync_playwright
from sqlalchemy import delete, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.core.database import SessionLocal  # noqa: E402
from app.models.conversation import Conversation  # noqa: E402
from app.models.message import Message  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.user_memory import MemoryExtractionJob, UserMemory  # noqa: E402
from app.models.user_setting import UserSetting  # noqa: E402
from app.repositories.user_repo import UserRepository  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402
from stage46a_real_e2e import cleanup  # noqa: E402

BASE = os.getenv("AIWS_E2E_BACKEND_URL", "http://127.0.0.1:32017")
WEB = os.getenv("AIWS_E2E_BASE_URL", "http://127.0.0.1:32018")
FIELDS = ("memory_enabled", "memory_auto_candidate_enabled", "memory_auto_activate_enabled", "memory_auto_candidate_turn_interval")


def run():
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == os.getenv("AIWS_E2E_USER_EMAIL", "1528713326@qq.com")))
        setting = db.scalar(select(UserSetting).where(UserSetting.user_id == user.id))
        assert setting.provider_type == "openai-compatible", "只使用在线模型"
        original = {key: getattr(setting, key) for key in FIELDS}
        user_id, model = user.id, setting.default_model
        token = AuthService(UserRepository(db)).create_access_token(user)
    client = httpx.Client(base_url=BASE, headers={"Authorization": f"Bearer {token}"}, timeout=240)
    project = None
    errors, result = [], {}

    def request(method, path, payload=None):
        response = client.request(method, path, json=payload)
        assert response.is_success, f"测试接口返回 {response.status_code}"
        return response.json()

    def await_memory(page, *, status, exclude_conversation=None):
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            with SessionLocal() as db:
                row = db.scalar(select(UserMemory).where(UserMemory.user_id == user_id,
                    UserMemory.project_id == project, UserMemory.status == status,
                    UserMemory.source_conversation_id != exclude_conversation if exclude_conversation else True))
                if row:
                    conv = row.source_conversation_id
                    memory_id = row.id
                    job = db.get(MemoryExtractionJob, row.extraction_job_id)
                    assert job and job.status == "succeeded", "通知关联任务未成功提交"
                    assistant = db.scalar(select(Message).where(Message.conversation_id == conv,
                        Message.role == "assistant").order_by(Message.created_at.desc()))
                    assert assistant and assistant.status == "done" and assistant.content, "真实最终回答未完成"
                    return conv, memory_id
            # Playwright 等待同时让浏览器网络和 UI 事件继续推进，不代执行 Worker。
            page.wait_for_timeout(500)
        raise AssertionError("独立 Worker 未生成预期记忆")

    try:
        request("PATCH", "/api/settings", {"memory_enabled": True, "memory_auto_candidate_enabled": True,
            "memory_auto_activate_enabled": True, "memory_auto_candidate_turn_interval": 1})
        project = request("POST", "/api/projects", {"name": f"memory-notification-{uuid4().hex[:8]}", "default_model": model})["id"]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            context.add_cookies([{"name": "aiws_token", "value": token, "url": WEB, "httpOnly": True, "sameSite": "Lax"}])
            page = context.new_page()
            page.on("pageerror", lambda _: errors.append("pageerror"))
            page.on("console", lambda item: errors.append("console_error") if item.type == "error" else None)
            page.goto(f"{WEB}/chat", wait_until="networkidle", timeout=60000)
            page.locator("select").nth(0).select_option(project)
            page.get_by_role("button", name="新对话", exact=True).click()
            page.get_by_placeholder("输入你的问题").fill("请记住：项目数据库采用 PostgreSQL 16。")
            page.get_by_role("button", name="发送消息", exact=True).click()
            conv, memory_id = await_memory(page, status="active")
            notice = page.get_by_test_id(f"memory-notice-{memory_id}")
            expect(notice).to_contain_text("已记住", timeout=20000)
            expect(notice).to_contain_text("PostgreSQL 16")
            result["automatic_notice_without_reload"] = "passed"
            print("真实后台生效通知：通过", flush=True)
            event_id = next(item["event_id"] for item in request("GET", f"/api/memories/activity/{conv}")["items"] if item["memory_id"] == memory_id)
            page.reload(wait_until="networkidle")
            expect(page.get_by_test_id(f"memory-notice-{memory_id}")).to_have_count(1, timeout=20000)
            page.get_by_test_id(f"memory-notice-{memory_id}").get_by_role("button", name="撤销记忆", exact=True).click()
            expect(page.get_by_test_id(f"memory-notice-{memory_id}")).to_contain_text("已撤销", timeout=20000)
            item = next(item for item in request("GET", f"/api/memories/activity/{conv}")["items"] if item["memory_id"] == memory_id)
            assert item["status"] == "revoked" and item["event_id"] == event_id
            result["refresh_deduplication_and_inline_revoke"] = "passed"
            print("刷新去重与聊天撤销：通过", flush=True)

            request("PATCH", "/api/settings", {"memory_auto_activate_enabled": False})
            page.get_by_role("button", name="新对话", exact=True).click()
            # 创建空会话前 project/model 可能随刷新恢复，明确保持隔离测试项目。
            page.locator("select").nth(0).select_option(project)
            expect(page.get_by_test_id(f"memory-notice-{memory_id}")).to_have_count(0)
            page.get_by_placeholder("输入你的问题").fill("请记住：项目缓存采用 Redis 7。")
            page.get_by_role("button", name="发送消息", exact=True).click()
            pending_conv, pending_id = await_memory(page, status="pending", exclude_conversation=conv)
            candidate_notice = page.get_by_test_id(f"memory-notice-{pending_id}")
            expect(candidate_notice).to_contain_text("待确认，尚未生效", timeout=20000)
            expect(candidate_notice).not_to_contain_text("已记住")
            expect(candidate_notice.get_by_role("button", name="撤销记忆", exact=True)).to_have_count(0)
            assert pending_conv != conv
            result["new_conversation_isolation_and_pending_not_saved"] = "passed"
            assert not errors, "浏览器发生 console/page error"
            result.update(real_chat_conversations=2, browser_errors=0, worker="independent_process")
            browser.close()
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        try:
            request("PATCH", "/api/settings", original)
        finally:
            client.close()
            if project:
                with SessionLocal() as db:
                    ids = list(db.scalars(select(Conversation.id).where(Conversation.project_id == project)))
                    db.execute(delete(UserMemory).where(UserMemory.project_id == project, UserMemory.user_id == user_id))
                    db.execute(delete(MemoryExtractionJob).where(MemoryExtractionJob.conversation_id.in_(ids)))
                    db.commit()
                cleanup(user_id=user_id, project_id=project)


if __name__ == "__main__":
    run()
