"""验证在线 Chat→buffer 低频复核→独立 Worker→证据候选与设置 UI；不保留临时资料。"""

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
from app.models.user_memory import MemoryExtractionJob, UserMemory  # noqa: E402
from app.models.conversation import Conversation  # noqa: E402
from app.models.memory_trigger_decision import MemoryTriggerDecision  # noqa: E402
from app.repositories.user_repo import UserRepository  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402
from stage46a_real_e2e import cleanup  # noqa: E402

BASE = os.getenv("AIWS_E2E_BACKEND_URL", "http://127.0.0.1:32007")
WEB = os.getenv("AIWS_E2E_BASE_URL", "http://127.0.0.1:32008")
FIELDS = ("memory_enabled", "memory_auto_candidate_enabled", "memory_auto_activate_enabled",
          "memory_auto_candidate_turn_interval", "memory_semantic_trigger_enabled")


def run():
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == os.getenv("AIWS_E2E_USER_EMAIL", "1528713326@qq.com")))
        setting = db.scalar(select(UserSetting).where(UserSetting.user_id == user.id))
        assert setting.provider_type == "openai-compatible", "只调用账号在线 API，不启动本地模型"
        original = {key: getattr(setting, key) for key in FIELDS}
        original_last = setting.memory_semantic_last_checked_at
        model, user_id = setting.default_model, user.id
        token = AuthService(UserRepository(db)).create_access_token(user)
    client = httpx.Client(base_url=BASE, headers={"Authorization": f"Bearer {token}"}, timeout=240)
    project, cases, browser_errors = None, [], []

    def request(method, path, payload=None):
        result = client.request(method, path, json=payload)
        assert result.is_success, f"接口未成功，状态码 {result.status_code}"
        return result.json()

    def chat(conv, text):
        deltas, done = [], False
        with chat_stream(client, "/api/chat/runs", json={"conversation_id": conv,
                "content": text + " 回复一句收到即可。", "model_name": model}) as response:
            assert response.is_success
            for event in iter_sse_events(response.iter_lines()):
                assert event["type"] not in {"error", "stream_error", "model_error"}, "在线回答失败"
                if event["type"] == "answer_delta":
                    deltas.append(event["text"])
                if event["type"] == "done":
                    done = True
        assert done and deltas

    def await_job(conv):
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            jobs = request("GET", "/api/memories/extraction-jobs")
            found = next((row for row in jobs if row["conversation_id"] == conv), None)
            if found and found["status"] == "failed":
                raise AssertionError(f"记忆任务失败 {found['error_code']}")
            if found and found["status"] == "succeeded":
                return found
            time.sleep(0.5)
        raise AssertionError("独立 Worker 未完成")

    try:
        request("PATCH", "/api/settings", dict(memory_enabled=True, memory_auto_candidate_enabled=True,
            memory_auto_activate_enabled=False, memory_semantic_trigger_enabled=False, memory_auto_candidate_turn_interval=4))
        project = request("POST", "/api/projects", {"name": f"memory-review-{uuid4().hex[:8]}", "default_model": model})["id"]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            context.add_cookies([{"name": "aiws_token", "value": token, "url": WEB, "httpOnly": True, "sameSite": "Lax"}])
            page = context.new_page()
            page.on("pageerror", lambda _: browser_errors.append("pageerror"))
            page.on("console", lambda entry: browser_errors.append("console_error") if entry.type == "error" else None)
            page.goto(f"{WEB}/settings?tab=memory", wait_until="networkidle", timeout=60000)
            checkbox = page.get_by_role("checkbox", name="低频语义复核缓冲信息", exact=False)
            expect(checkbox).not_to_be_checked()
            checkbox.check()
            page.get_by_role("button", name="保存设置", exact=True).click()
            expect(checkbox).to_be_checked()
            page.reload(wait_until="networkidle")
            expect(checkbox).to_be_checked()
            assert request("GET", "/api/settings")["memory_semantic_trigger_enabled"]
            page.screenshot(path="/tmp/aiws-memory-semantic-setting.png", full_page=True)
            assert not browser_errors
            browser.close()
        cases.append({"case": "浏览器开启语义复核、保存与刷新保持", "status": "passed", "browser_errors": 0})
        # 只调整测试账号的复核冷却时间，不改变任何 Provider 或凭据配置。
        with SessionLocal() as db:
            db.scalar(select(UserSetting).where(UserSetting.user_id == user_id)).memory_semantic_last_checked_at = None
            db.commit()
        conv = request("POST", "/api/conversations", {"title": "在线缓冲语义复核", "model_name": model, "project_id": project})["id"]
        texts = ("数据库方案已经定稿为 PostgreSQL 16。", "评审会固定安排在每周三。",
                 "资料统一按年份归档。", "文档协作用 Markdown 格式。")
        for index, text in enumerate(texts):
            chat(conv, text)
            if index < 3:
                with SessionLocal() as db:
                    assert db.scalar(select(MemoryExtractionJob.id).where(MemoryExtractionJob.conversation_id == conv)) is None
        job = await_job(conv)
        assert job["trigger_action"] == "buffer" and job["semantic_model_called"]
        assert job["semantic_action"] == "extract_now", "在线模型未识别这些固定工作约定，保留本次失败供排查"
        with SessionLocal() as db:
            rows = list(db.scalars(select(UserMemory).where(UserMemory.extraction_job_id == job["id"])))
            assert rows and all(row.evidence_quote and row.status == "pending" for row in rows)
        cases.append({"case": "四轮真实 Chat→一次语义复核→原提取及原文核验待审", "status": "passed",
                      "semantic_action": job["semantic_action"], "verified_pending_candidates": len(rows)})
        decisions = request("GET", f"/api/memories/trigger-decisions?conversation_id={conv}")
        assert decisions and all("content" not in row and "source_digest" not in row for row in decisions)
        labeled = request("POST", f"/api/memories/trigger-decisions/{decisions[0]['id']}/label",
                          {"expected_action": "buffer", "review_reason": "correct"})
        assert labeled["expected_action"] == "buffer" and labeled["reviewed_at"]
        cases.append({"case": "真实观测查询和人工标签接口，无正文复制", "status": "passed"})
        ordinary = request("POST", "/api/conversations", {"title": "普通问题不调用语义判断", "model_name": model, "project_id": project})["id"]
        chat(ordinary, "什么是内存？")
        with SessionLocal() as db:
            assert db.scalar(select(MemoryExtractionJob.id).where(MemoryExtractionJob.conversation_id == ordinary)) is None
            decision = db.scalar(select(MemoryTriggerDecision).where(MemoryTriggerDecision.conversation_id == ordinary))
            assert decision and decision.action == "skip" and not decision.semantic_model_called
        cases.append({"case": "普通问答零提取 Job、零语义复核", "status": "passed"})
        result = {"model": model, "conversations": 2, "chat_turns": 5, "cases": cases,
            "provenance": "controlled_online_scenarios_not_organic_user_labels",
            "scope": "真实在线模型、独立记忆Worker、PostgreSQL、浏览器；不是通用模型准确率或计费调用量"}
        output = Path(os.getenv("AIWS_MEMORY_REVIEW_REPORT", "/tmp/aiws-memory-review-real.json"))
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        try:
            request("PATCH", "/api/settings", original)
        finally:
            client.close()
            with SessionLocal() as db:
                db.scalar(select(UserSetting).where(UserSetting.user_id == user_id)).memory_semantic_last_checked_at = original_last
                if project:
                    ids = list(db.scalars(select(Conversation.id).where(Conversation.project_id == project)))
                    db.execute(delete(UserMemory).where(UserMemory.user_id == user_id, UserMemory.project_id == project))
                    db.execute(delete(MemoryExtractionJob).where(MemoryExtractionJob.conversation_id.in_(ids)))
                db.commit()
            if project:
                cleanup(user_id=user_id, project_id=project)


if __name__ == "__main__":
    run()
