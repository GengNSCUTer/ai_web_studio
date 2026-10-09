"""第二步真实验收：在线 Chat/提取、浏览器授权、自动生效、增量和撤销。

固定账号仅临时开启相关设置；只创建独立临时项目，最终恢复设置并清理数据。
不输出凭证、完整模型回复或用户记忆，不启动本地模型。
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch
from uuid import uuid4

import httpx
from playwright.sync_api import sync_playwright, expect
from sqlalchemy import delete, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.core.database import SessionLocal  # noqa: E402
from app.models.conversation import Conversation  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.user_setting import UserSetting  # noqa: E402
from app.models.user_memory import MemoryExtractionJob, UserMemory  # noqa: E402
from app.repositories.memory_job_repo import MemoryExtractionJobRepository  # noqa: E402
from app.repositories.user_repo import UserRepository  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402
from app.services.memory_candidate_runtime import MemoryCandidateWorker  # noqa: E402
from stage46a_real_e2e import cleanup  # noqa: E402

BASE = os.getenv("AIWS_E2E_BACKEND_URL", "http://127.0.0.1:32017").rstrip("/")
WEB = os.getenv("AIWS_E2E_BASE_URL", "http://127.0.0.1:32018").rstrip("/")
FIELDS = ("memory_enabled", "memory_auto_candidate_enabled", "memory_auto_activate_enabled", "memory_auto_candidate_turn_interval")


def run():
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == os.getenv("AIWS_E2E_USER_EMAIL", "1528713326@qq.com")))
        assert user, "固定账号不存在"
        setting = db.scalar(select(UserSetting).where(UserSetting.user_id == user.id))
        assert setting.provider_type == "openai-compatible", "仅使用现有在线 Provider"
        before = {name: getattr(setting, name) for name in FIELDS}
        token = AuthService(UserRepository(db)).create_access_token(user)
        user_id, model = user.id, setting.default_model
    client = httpx.Client(base_url=BASE, headers={"Authorization": f"Bearer {token}"}, timeout=240)
    project_id = None
    errors, summary = [], {}

    def request(method, path, payload=None):
        response = client.request(method, path, json=payload)
        assert response.is_success, f"测试 API {method} {path} 返回 {response.status_code}"
        return response.json() if response.content else None

    def conversation():
        return request("POST", "/api/conversations", {"title": "记忆第二步验收", "model_name": model, "project_id": project_id})["id"]

    def chat(conv, content):
        answer, done = [], False
        with client.stream("POST", "/api/chat/events-stream", json={"conversation_id": conv, "content": content, "model_name": model}) as response:
            assert response.is_success, "真实 Chat 未启动"
            for line in response.iter_lines():
                if not line.strip():
                    continue
                event = json.loads(line)
                assert event["type"] != "error", "真实 Chat 出错"
                if event["type"] == "answer_delta":
                    answer.append(event["text"])
                if event["type"] == "done":
                    done = True
        assert done and answer, "真实 Chat 未生成最终回答"
        return "".join(answer)

    def worker(job_id):
        if os.getenv("AIWS_MEMORY_E2E_EXTERNAL_WORKER") == "1":
            # 独立进程验收：仅观察，不在测试脚本里代执行后台工作。
            deadline = time.monotonic() + 240
            while time.monotonic() < deadline:
                with SessionLocal() as db:
                    state = db.get(MemoryExtractionJob, job_id).status
                if state == "succeeded":
                    return
                assert state != "failed", "独立 Worker 提取失败"
                time.sleep(1)
            raise AssertionError("独立 Worker 未在时限内完成")
        # 只领取本次测试任务；提取模型、租约、证据检查和数据库操作均为真实实现。
        def claim(repo, owner, *, lease_seconds=90):
            return repo.db.scalar(select(MemoryExtractionJob).where(MemoryExtractionJob.id == job_id,
                MemoryExtractionJob.status == "pending").with_for_update())
        with patch.object(MemoryExtractionJobRepository, "claim_next", claim):
            # Playwright 同步 API 内部已有事件循环，Worker 在独立线程使用自己的循环。
            with ThreadPoolExecutor(max_workers=1) as pool:
                assert pool.submit(lambda: asyncio.run(MemoryCandidateWorker(owner="memory-step2-e2e").run_once())).result(timeout=240)
        with SessionLocal() as db:
            assert db.get(MemoryExtractionJob, job_id).status == "succeeded", "在线提取任务未成功"

    try:
        request("PATCH", "/api/settings", {"memory_enabled": True, "memory_auto_candidate_enabled": True,
            "memory_auto_activate_enabled": False, "memory_auto_candidate_turn_interval": 50})
        project_id = request("POST", "/api/projects", {"name": f"memory-step2-e2e-{uuid4().hex[:8]}", "default_model": model})["id"]
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            context.add_cookies([{"name": "aiws_token", "value": token, "url": WEB, "httpOnly": True, "sameSite": "Lax"}])
            page = context.new_page()
            page.on("pageerror", lambda _: errors.append("pageerror"))
            page.on("console", lambda item: errors.append("console_error") if item.type == "error" else None)
            page.goto(f"{WEB}/settings", wait_until="networkidle", timeout=60000)
            page.get_by_role("button", name="长期记忆", exact=True).click()
            toggle = page.get_by_role("checkbox", name="自动启用有原文证据的低风险事实")
            expect(toggle).not_to_be_checked()
            toggle.check()
            page.get_by_role("button", name="保存设置", exact=True).click()
            expect(page.get_by_text("设置已保存", exact=True)).to_be_visible(timeout=20000)
            assert request("GET", "/api/settings")["memory_auto_activate_enabled"]
            summary["browser_opt_in_persisted"] = "passed"

            source = conversation()
            chat(source, "请记住：项目数据库采用 PostgreSQL 16。")
            jobs = request("GET", "/api/memories/extraction-jobs")
            job = next((item for item in jobs if item["conversation_id"] == source), None)
            assert job, "明确记住请求未自动触发任务"
            worker(job["id"])
            memories = request("GET", "/api/memories")
            saved = next((item for item in memories if item["source_conversation_id"] == source and item["status"] == "active"), None)
            assert saved and saved["source"] == "auto_confirmed" and saved["evidence_quote"], "明确事实未通过原文核验自动启用"
            assert "," not in saved["source_message_ids"], "记忆来源仍然是整批消息"
            summary["online_chat_worker_evidence_auto_activation"] = "passed"

            answer = chat(conversation(), "根据已记住的项目背景，用一句话告诉我项目数据库及版本；不知道则说不知道。不要调用工具。")
            assert "PostgreSQL" in answer and "16" in answer, "新会话最终回答未使用已启用事实"
            summary["cross_conversation_final_answer"] = "passed"
            response = client.post(f"/api/memories/extraction-jobs/{source}")
            assert response.status_code == 409, "已处理消息被重复提取"
            summary["processed_source_not_reenqueued"] = "passed"

            page.reload(wait_until="networkidle")
            page.get_by_role("button", name="长期记忆", exact=True).click()
            card = page.locator("div.rounded-2xl").filter(has=page.get_by_text(saved["content"], exact=True)).filter(has=page.get_by_role("button", name="撤销记忆", exact=True)).last
            expect(card.get_by_text("按已开启的自动模式记住，可撤销", exact=True)).to_be_visible()
            expect(card.get_by_text(f"用户原话：{saved['evidence_quote']}", exact=True)).to_be_visible()
            card.get_by_role("button", name="撤销记忆", exact=True).click()
            expect(page.get_by_text("长期记忆已撤销", exact=True)).to_be_visible(timeout=20000)
            summary["browser_evidence_notice_and_revoke"] = "passed"

            chat(source, "请记住：项目数据库采用 PostgreSQL 16。")
            jobs = request("GET", "/api/memories/extraction-jobs")
            next_job = next(item for item in jobs if item["conversation_id"] == source and item["id"] != job["id"])
            worker(next_job["id"])
            memories = [item for item in request("GET", "/api/memories") if item["source_conversation_id"] == source]
            assert not any(item["status"] == "active" for item in memories), "撤销过的事实被自动重新启用"
            assert next_job["cursor_end"] > job["cursor_end"], "新任务未使用增量范围"
            summary["incremental_new_source_and_revocation_history"] = "passed"

            request("PATCH", "/api/settings", {"memory_auto_candidate_turn_interval": 1})
            ordinary = conversation()
            chat(ordinary, "项目缓存采用 Redis 7。")
            jobs = request("GET", "/api/memories/extraction-jobs")
            ordinary_job = next(item for item in jobs if item["conversation_id"] == ordinary)
            worker(ordinary_job["id"])
            facts = request("GET", "/api/memories")
            ordinary_fact = next((item for item in facts if item["source_conversation_id"] == ordinary
                                  and item["status"] == "active"), None)
            assert ordinary_fact and ordinary_fact["source"] == "auto_confirmed", "普通明确陈述未在 opt-in 后自动生效"
            summary["ordinary_stable_statement_auto_activation"] = "passed"

            manual = request("POST", "/api/memories/remember", {"memory_type": "fact", "title": "验收项目代号", "content": "验收项目代号为 MemoryStepTwo", "project_id": project_id})
            corrected = request("PATCH", f"/api/memories/{manual['id']}", {"content": "验收项目代号为 MemoryStepTwoUpdated", "expected_version": manual["version"]})
            assert corrected["id"] != manual["id"] and corrected["supersedes_memory_id"] == manual["id"]
            forgotten = request("POST", f"/api/memories/{corrected['id']}/forget")
            assert forgotten["status"] == "revoked"
            summary["explicit_remember_correct_forget_api"] = "passed"
            assert not errors, "浏览器出现错误"
            summary["browser_errors"] = len(errors)
            summary["worker_mode"] = "independent_process" if os.getenv("AIWS_MEMORY_E2E_EXTERNAL_WORKER") == "1" else "real_runtime_scoped_claim"
            browser.close()
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        try:
            request("PATCH", "/api/settings", before)
        finally:
            client.close()
            if project_id:
                with SessionLocal() as db:
                    ids = list(db.scalars(select(Conversation.id).where(Conversation.project_id == project_id)))
                    db.execute(delete(MemoryExtractionJob).where(MemoryExtractionJob.conversation_id.in_(ids)))
                    memories = list(db.scalars(select(UserMemory).where(UserMemory.project_id == project_id, UserMemory.user_id == user_id)))
                    for item in memories:
                        item.supersedes_memory_id = None
                    db.flush()
                    for item in memories:
                        db.delete(item)
                    db.commit()
                cleanup(user_id=user_id, project_id=project_id)


if __name__ == "__main__":
    run()
