"""不依赖模型额度的真实撤销 Worker/浏览器验收；来源消息为明确标记的测试夹具。"""

import json
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
from app.models.user_memory import UserMemory, MemoryExtractionJob  # noqa: E402
from app.models.user_setting import UserSetting  # noqa: E402
from app.repositories.user_repo import UserRepository  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402
from app.services.memory_candidate_runtime import MemoryExtractionJobService  # noqa: E402
from stage46a_real_e2e import cleanup  # noqa: E402
from memory_trigger_real_e2e import BASE, WEB, FIELDS  # noqa: E402


def run():
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "1528713326@qq.com"))
        setting = db.scalar(select(UserSetting).where(UserSetting.user_id == user.id))
        original = {key: getattr(setting, key) for key in FIELDS}
        user_id, model = user.id, setting.default_model
        token = AuthService(UserRepository(db)).create_access_token(user)
    project, errors = None, []
    client = httpx.Client(base_url=BASE, headers={"Authorization": f"Bearer {token}"}, timeout=30)

    def request(method, path, payload=None):
        response = client.request(method, path, json=payload)
        assert response.is_success, f"测试接口返回 {response.status_code}"
        return response.json()

    def control(title, text):
        conv = request("POST", "/api/conversations", {"title": title, "model_name": model, "project_id": project})["id"]
        with SessionLocal() as db:
            source = Message(conversation_id=conv, role="user", sequence=1, content=text)
            assistant = Message(conversation_id=conv, role="assistant", sequence=2, status="done",
                                content="受控测试夹具：此占位未调用在线模型。")
            db.add_all([source, assistant])
            db.commit()
            job = MemoryExtractionJobService(db).enqueue_after_turn(user_id=user_id,
                conversation_id=conv, assistant_message_id=assistant.id)
            job_id = job.id
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            activity = request("GET", f"/api/memories/activity/{conv}")
            if activity["items"]:
                return conv, job_id, activity["items"][0]
            time.sleep(0.25)
        raise AssertionError("独立 Worker 未提交控制请求")

    try:
        request("PATCH", "/api/settings", {"memory_enabled": True, "memory_auto_candidate_enabled": False})
        project = request("POST", "/api/projects", {"name": f"memory-control-{uuid4().hex[:8]}", "default_model": model})["id"]
        with SessionLocal() as db:
            memory = UserMemory(user_id=user_id, project_id=project, title="测试偏好", content="偏好分点回答。",
                                status="active", is_enabled=True)
            db.add(memory)
            db.commit()
            memory_id = memory.id
        _, _, precise = control("精确撤销测试", "请忘记：偏好分点回答。")
        assert precise["status"] == "revoked"
        with SessionLocal() as db:
            assert db.get(UserMemory, memory_id).status == "revoked"
            assert db.get(UserMemory, memory_id).version == 2
        conv, job_id, ambiguous = control("模糊撤销测试", "请忘记：我的数据库偏好")
        assert ambiguous["status"] == "needs_review"
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            context.add_cookies([{"name": "aiws_token", "value": token, "url": WEB, "httpOnly": True, "sameSite": "Lax"}])
            page = context.new_page()
            page.on("pageerror", lambda _: errors.append("pageerror"))
            page.on("console", lambda item: errors.append("console_error") if item.type == "error" else None)
            page.goto(f"{WEB}/chat", wait_until="networkidle", timeout=60000)
            page.locator("select").nth(0).select_option(project)
            page.get_by_role("button", name="模糊撤销测试", exact=False).first.click()
            page.get_by_role("button", name="展开记忆通知", exact=True).click()
            expect(page.get_by_test_id(f"memory-notice-{job_id}")).to_contain_text("需要确认", timeout=20000)
            expect(page.get_by_role("link", name="管理记忆")).to_have_attribute("href", "/settings?tab=memory")
            page.screenshot(path="/tmp/aiws-memory-control-20261111.png", full_page=True)
            assert not errors, "浏览器出现异常"
            browser.close()
        print(json.dumps({"precise_revocation": "passed", "ambiguous_review": "passed", "browser_notice": "passed",
            "browser_errors": 0, "worker": "independent_process", "extraction_model_calls": 0,
            "source": "synthetic_completed_turn_fixture_not_real_chat"}, ensure_ascii=False))
    finally:
        try:
            request("PATCH", "/api/settings", original)
        finally:
            client.close()
            if project:
                with SessionLocal() as db:
                    ids = list(db.scalars(select(Conversation.id).where(Conversation.project_id == project)))
                    db.execute(delete(UserMemory).where(UserMemory.user_id == user_id, UserMemory.project_id == project))
                    db.execute(delete(MemoryExtractionJob).where(MemoryExtractionJob.conversation_id.in_(ids)))
                    db.commit()
                cleanup(user_id=user_id, project_id=project)


if __name__ == "__main__":
    run()
