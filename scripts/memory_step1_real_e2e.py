"""记忆第一步真实验收：在线提取、浏览器确认、跨会话回答、临时覆盖和撤销。

仅使用固定账号的现有在线 Provider，创建独立临时项目；最终清理测试数据。
授权凭证仅留在内存，不输出模型原文、账号配置或 Token。
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import re
import sys
import time
from unittest.mock import patch
from uuid import uuid4

import httpx
from playwright.sync_api import sync_playwright, expect
from sqlalchemy import delete, select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.database import SessionLocal  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.user_setting import UserSetting  # noqa: E402
from app.models.user_memory import MemoryExtractionJob, UserMemory  # noqa: E402
from app.models.conversation import Conversation  # noqa: E402
from app.repositories.memory_job_repo import MemoryExtractionJobRepository  # noqa: E402
from app.repositories.user_repo import UserRepository  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402
from app.services.memory_candidate_runtime import MemoryCandidateWorker  # noqa: E402
from app.services.memory_policy import memory_identity  # noqa: E402
from stage46a_real_e2e import cleanup  # noqa: E402

BASE = os.getenv("AIWS_E2E_BACKEND_URL", "http://127.0.0.1:32017").rstrip("/")
WEB = os.getenv("AIWS_E2E_BASE_URL", "http://127.0.0.1:32018").rstrip("/")


def run():
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == os.getenv("AIWS_E2E_USER_EMAIL", "1528713326@qq.com")))
        assert user is not None, "固定测试账号不存在"
        setting = db.scalar(select(UserSetting).where(UserSetting.user_id == user.id))
        assert setting and setting.provider_type == "openai-compatible", "需要在线模型"
        memory_enabled_before = bool(setting.memory_enabled)
        if not memory_enabled_before:
            # 测试只临时打开注入开关，finally 会恢复固定账号原状态。
            setting.memory_enabled = True
            db.commit()
        token = AuthService(UserRepository(db)).create_access_token(user)
        user_id, model = user.id, setting.default_model
    client = httpx.Client(base_url=BASE, headers={"Authorization": f"Bearer {token}"}, timeout=240)
    project_id = None
    summary = {}
    errors = []

    def request(method, path, payload=None):
        response = client.request(method, path, json=payload)
        assert response.is_success, f"测试 API {method} {path} 返回 {response.status_code}"
        return response.json() if response.content else None

    def conversation():
        return request("POST", "/api/conversations", {"title": "记忆第一步验收", "model_name": model, "project_id": project_id})["id"]

    def chat(conv_id, content):
        with client.stream("POST", "/api/chat/events-stream", json={"conversation_id": conv_id, "content": content, "model_name": model}, headers={"Accept": "application/x-ndjson"}) as response:
            assert response.is_success, f"Chat API 返回 {response.status_code}"
            answer, done = [], False
            for line in response.iter_lines():
                if not line.strip():
                    continue
                event = json.loads(line)
                if event["type"] == "answer_delta":
                    answer.append(event["text"])
                if event["type"] == "error":
                    raise AssertionError("在线 Chat 返回错误事件")
                if event["type"] == "done":
                    done = True
            assert done and answer, "Chat 未完成最终回答"
            return "".join(answer)

    try:
        project_id = request("POST", "/api/projects", {"name": f"memory-step1-e2e-{uuid4().hex[:10]}", "default_model": model})["id"]
        source = conversation()
        title = "默认回答语言"
        old = request("POST", "/api/memories", {"memory_type": "profile", "title": title, "content": "用户默认喜欢中文回答", "source_conversation_id": source})
        chat(source, "我已经改变了长期语言偏好，以后默认请用英文回答。请只简短确认收到，不调用任何工具。")
        job = request("POST", f"/api/memories/extraction-jobs/{source}")

        # 只领取本次测试 Job；提取模型、Worker 处理、租约和写入均使用真实实现。
        def claim_test_job(repo, owner, *, lease_seconds=90):
            return repo.db.scalar(select(MemoryExtractionJob).where(MemoryExtractionJob.id == job["id"]).with_for_update())
        if os.getenv("AIWS_MEMORY_E2E_EXTERNAL_WORKER") == "1":
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                with SessionLocal() as db:
                    state = db.get(MemoryExtractionJob, job["id"]).status
                if state == "succeeded":
                    break
                assert state != "failed", "独立 Worker 提取失败"
                time.sleep(1)
            else:
                raise AssertionError("独立 Worker 提取超时")
        else:
            with patch.object(MemoryExtractionJobRepository, "claim_next", claim_test_job):
                assert asyncio.run(MemoryCandidateWorker(owner="memory-step1-real-e2e").run_once())
        with SessionLocal() as db:
            stored_job = db.get(MemoryExtractionJob, job["id"])
            assert stored_job.status == "succeeded", "真实提取任务失败"
            candidates = list(db.scalars(select(UserMemory).where(UserMemory.source_conversation_id == source, UserMemory.status == "pending")))
            candidate = next((item for item in candidates if memory_identity(item.memory_type, item.title, item.content).key == "response_language" and memory_identity(item.memory_type, item.title, item.content).value == "en"), None)
            assert candidate and candidate.risk_level == "conflict" and candidate.supersedes_memory_id == old["id"], "提取的语言变更没有进入正确冲突审核"
            candidate_id, candidate_content = candidate.id, candidate.content
            summary["extraction_job"] = "succeeded"
            summary["candidate_classification"] = "conflict"

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            context.add_cookies([{"name": "aiws_token", "value": token, "url": WEB, "httpOnly": True, "sameSite": "Lax"}])
            page = context.new_page()
            page.on("pageerror", lambda _: errors.append("pageerror"))
            page.on("console", lambda event: errors.append("console_error") if event.type == "error" else None)
            page.goto(f"{WEB}/settings", wait_until="networkidle", timeout=60000)
            page.get_by_role("button", name="长期记忆", exact=True).click()
            card = page.locator("div.rounded-2xl").filter(has=page.get_by_text(candidate_content, exact=True)).filter(has=page.get_by_role("button", name="确认启用", exact=True)).last
            card.get_by_role("button", name="确认启用", exact=True).click()
            expect(page.get_by_text("候选记忆已确认并启用", exact=True)).to_be_visible(timeout=20000)
            old_card = page.locator("div.rounded-2xl").filter(has=page.get_by_text(old["content"], exact=True)).last
            expect(old_card.get_by_text("superseded", exact=True)).to_be_visible()
            summary["browser_approval_and_old_version_refresh"] = "passed"

            answer = chat(conversation(), "简短解释什么是数据库事务，不调用工具。")
            assert re.search(r"[a-zA-Z]{4,}", answer) and not re.search(r"[\u4e00-\u9fff]", answer), "跨会话未使用新英文偏好"
            summary["cross_conversation_english_answer"] = "passed"
            answer = chat(conversation(), "这次请用中文简短解释什么是数据库事务，不调用工具。")
            assert re.search(r"[\u4e00-\u9fff]", answer), "当前中文要求未覆盖保存偏好"
            summary["current_turn_override"] = "passed"
            with SessionLocal() as db:
                active = db.get(UserMemory, candidate_id)
                assert active.status == "active" and memory_identity(active.memory_type, active.title, active.content).value == "en"
                assert db.get(UserMemory, old["id"]).status == "superseded"
            summary["temporary_request_did_not_mutate_memory"] = "passed"

            approved_card = page.locator("div.rounded-2xl").filter(has=page.get_by_text(candidate_content, exact=True)).filter(has=page.get_by_role("button", name="撤销记忆", exact=True)).last
            approved_card.get_by_role("button", name="撤销记忆", exact=True).click()
            expect(page.get_by_text("长期记忆已撤销", exact=True)).to_be_visible(timeout=20000)
            summary["browser_revoke"] = "passed"
            with SessionLocal() as db:
                assert db.get(UserMemory, candidate_id).status == "revoked"
            assert not errors, "浏览器出现错误"
            summary["browser_errors"] = len(errors)
            browser.close()
        rejected = client.post("/api/memories", json={"memory_type": "fact", "title": "测试安全边界", "content": "password=fake-only-for-test", "project_id": project_id})
        assert rejected.status_code == 409, "真实 API 未拒绝凭证"
        summary["credential_api_rejection"] = "passed"
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        client.close()
        if project_id:
            with SessionLocal() as db:
                conv_ids = list(db.scalars(select(Conversation.id).where(Conversation.project_id == project_id)))
                if conv_ids:
                    db.execute(delete(MemoryExtractionJob).where(MemoryExtractionJob.conversation_id.in_(conv_ids)))
                # 后继版本先解除仅本次临时数据的外键，再删除，真实用户记忆不动。
                memories = list(db.scalars(select(UserMemory).where(UserMemory.user_id == user_id, UserMemory.project_id == project_id)))
                for memory in memories:
                    memory.supersedes_memory_id = None
                db.flush()
                for memory in memories:
                    db.delete(memory)
                db.commit()
            cleanup(user_id=user_id, project_id=project_id)
        if not memory_enabled_before:
            with SessionLocal() as db:
                restored = db.scalar(select(UserSetting).where(UserSetting.user_id == user_id))
                if restored:
                    restored.memory_enabled = False
                    db.commit()


if __name__ == "__main__":
    run()
