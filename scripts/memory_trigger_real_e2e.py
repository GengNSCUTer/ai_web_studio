"""在线对话→独立记忆 Worker→持久化与浏览器通知；只清理临时项目。"""

import json
from chat_sse_client import chat_stream, iter_sse_events
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
from app.models.user import User  # noqa: E402
from app.models.user_setting import UserSetting  # noqa: E402
from app.models.conversation import Conversation  # noqa: E402
from app.models.user_memory import MemoryExtractionJob, UserMemory  # noqa: E402
from app.repositories.user_repo import UserRepository  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402
from stage46a_real_e2e import cleanup  # noqa: E402

BASE = os.getenv("AIWS_E2E_BACKEND_URL", "http://127.0.0.1:32007")
WEB = os.getenv("AIWS_E2E_BASE_URL", "http://127.0.0.1:32008")
FIELDS = ("memory_enabled", "memory_auto_candidate_enabled", "memory_auto_activate_enabled", "memory_auto_candidate_turn_interval")


def run():
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == os.getenv("AIWS_E2E_USER_EMAIL", "1528713326@qq.com")))
        setting = db.scalar(select(UserSetting).where(UserSetting.user_id == user.id))
        assert setting.provider_type == "openai-compatible", "仅允许配置好的在线 API"
        original = {key: getattr(setting, key) for key in FIELDS}
        user_id, model = user.id, setting.default_model
        token = AuthService(UserRepository(db)).create_access_token(user)
    client = httpx.Client(base_url=BASE, headers={"Authorization": f"Bearer {token}"}, timeout=240)
    project, cases, errors = None, [], []

    def request(method, path, payload=None):
        response = client.request(method, path, json=payload)
        assert response.is_success, f"测试接口状态异常 {response.status_code}"
        return response.json()

    def conversation(title):
        return request("POST", "/api/conversations", {"title": title, "model_name": model, "project_id": project})["id"]

    def chat(conv, question):
        text, done, details = [], False, {}
        with chat_stream(client, "/api/chat/runs", json={"conversation_id": conv,
                "content": question, "model_name": model}) as response:
            assert response.is_success
            for event in iter_sse_events(response.iter_lines()):
                assert event["type"] not in {"error", "stream_error", "model_error"}, "真实模型生成失败"
                if event["type"] == "answer_delta":
                    text.append(event["text"])
                elif event["type"] == "context_info":
                    details = event["info"]["details"]
                elif event["type"] == "done":
                    done = True
        assert done and text, "最终回答未完成"
        return "".join(text), details

    def await_job(conv, expected):
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            rows = request("GET", "/api/memories/extraction-jobs")
            row = next((row for row in rows if row["conversation_id"] == conv), None)
            if row and row["status"] == "failed":
                raise AssertionError(f"后台任务失败 {row['error_code']}")
            if row and row["status"] == "succeeded":
                assert row["trigger_reason"] == expected
                return row
            time.sleep(0.5)
        raise AssertionError("独立记忆 Worker 未在时限内完成")

    try:
        request("PATCH", "/api/settings", dict(memory_enabled=True, memory_auto_candidate_enabled=True,
            memory_auto_activate_enabled=True, memory_auto_candidate_turn_interval=4))
        project = request("POST", "/api/projects", {"name": f"memory-trigger-{uuid4().hex[:8]}", "default_model": model})["id"]
        ordinary = conversation("纯问答不提取")
        for text in ("什么是缓存？一句话", "什么是事务？一句话", "如何理解索引？一句话", "谢谢，简短回复"):
            chat(ordinary, text)
        with SessionLocal() as db:
            assert db.scalar(select(MemoryExtractionJob.id).where(MemoryExtractionJob.conversation_id == ordinary)) is None
        cases.append({"case": "四轮普通问答", "extraction_jobs": 0, "status": "passed"})

        stable = conversation("稳定事实首轮提取")
        chat(stable, "项目采用 PostgreSQL 16。")
        job = await_job(stable, "stable_statement")
        with SessionLocal() as db:
            row = db.scalar(select(UserMemory).where(UserMemory.extraction_job_id == job["id"]))
            assert row and row.status == "active" and row.evidence_quote
            stable_memory = row.id
        cases.append({"case": "稳定事实首轮提取及原文核验生效", "status": "passed"})
        reuse = conversation("新会话使用记忆")
        answer, details = chat(reuse, "项目采用哪种数据库和版本？只根据已有背景回答。")
        assert "PostgreSQL" in answer and "16" in answer
        assert stable_memory in {item["id"] for item in details["memory_retrieval"]["injected"]}
        cases.append({"case": "新会话召回实际已启用记忆", "status": "passed"})

        request("PATCH", "/api/settings", {"memory_auto_candidate_enabled": False})
        explicit = conversation("显式请求不受自动提取开关影响")
        chat(explicit, "请记住：项目缓存采用 Redis 7。")
        job = await_job(explicit, "explicit_remember")
        with SessionLocal() as db:
            row = db.scalar(select(UserMemory).where(UserMemory.extraction_job_id == job["id"]))
            assert row and row.status == "pending" and row.evidence_quote
            pending_id, pending_content = row.id, row.content
        cases.append({"case": "自动提取关闭仍接受显式请求且保持待审", "status": "passed"})

        ambiguous = conversation("模糊忘记交给用户确认")
        chat(ambiguous, "请忘记：我之前的数据库偏好")
        control_job = await_job(ambiguous, "explicit_forget")
        activity = request("GET", f"/api/memories/activity/{ambiguous}")
        assert any(item["status"] == "needs_review" for item in activity["items"])
        with SessionLocal() as db:
            assert db.get(UserMemory, stable_memory).status == "active"
        cases.append({"case": "模糊撤销没有误删", "status": "passed"})

        exact = conversation("精确撤销无需提取模型")
        chat(exact, f"请忘记：{pending_content}")
        await_job(exact, "explicit_forget")
        with SessionLocal() as db:
            assert db.get(UserMemory, pending_id).status == "revoked"
        cases.append({"case": "精确撤销当前范围待审记忆", "status": "passed"})

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            context.add_cookies([{"name": "aiws_token", "value": token, "url": WEB, "httpOnly": True, "sameSite": "Lax"}])
            page = context.new_page()
            page.on("pageerror", lambda _: errors.append("pageerror"))
            page.on("console", lambda item: errors.append("console_error") if item.type == "error" else None)
            page.goto(f"{WEB}/chat", wait_until="networkidle", timeout=60000)
            page.locator("select").nth(0).select_option(project)
            # 从实际会话列表选择，检查真实 activity 返回及新通知样式。
            page.get_by_role("button", name="模糊忘记交给用户确认", exact=False).first.click()
            page.get_by_role("button", name="展开记忆通知", exact=True).click()
            expect(page.get_by_test_id(f"memory-notice-{control_job['id']}")).to_contain_text("需要确认", timeout=20000)
            expect(page.get_by_role("link", name="管理记忆")).to_have_attribute("href", "/settings?tab=memory")
            page.screenshot(path="/tmp/aiws-memory-trigger-notice-20261111.png", full_page=True)
            assert not errors, "浏览器发生错误"
            browser.close()
        cases.append({"case": "真实浏览器控制请求通知与管理入口", "status": "passed", "browser_errors": 0})
        result = {"model": model, "conversations": 6, "chat_turns": 9, "cases": cases,
                  "scope": "临时项目、真实在线 Chat/独立 Worker/数据库/浏览器；不代表线上通用准确率"}
        output = Path(os.getenv("AIWS_MEMORY_E2E_REPORT", "/tmp/aiws-memory-trigger-real-20261111.json"))
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
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
