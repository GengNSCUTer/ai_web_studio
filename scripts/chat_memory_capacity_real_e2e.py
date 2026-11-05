"""固定账号、在线模型验证千条历史的对话/重复召回/重生成/编辑重答与浏览器。"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from uuid import uuid4

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from sqlalchemy import delete, insert, select
from app.core.database import SessionLocal
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User
from app.models.user_memory import UserMemory
from app.models.user_setting import UserSetting
from app.repositories.user_repo import UserRepository
from app.services.auth_service import AuthService


def run():
    from playwright.sync_api import expect, sync_playwright
    from stage46a_real_e2e import cleanup
    base = os.getenv("AIWS_E2E_BACKEND_URL", "http://127.0.0.1:32007")
    web = os.getenv("AIWS_E2E_BASE_URL", "http://127.0.0.1:32008")
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == os.getenv("AIWS_E2E_USER_EMAIL", "1528713326@qq.com")))
        setting = db.scalar(select(UserSetting).where(UserSetting.user_id == user.id))
        assert setting.provider_type == "openai-compatible" and setting.knowledge_embedding_provider != "ollama"
        assert setting.memory_enabled
        token = AuthService(UserRepository(db)).create_access_token(user)
        uid, model = user.id, setting.default_model
    client = httpx.Client(base_url=base, headers={"Authorization": f"Bearer {token}"}, timeout=120)
    project, memory = None, None
    report = []
    try:
        response = client.post("/api/projects", json={"name": f"容量端到端验收-{uuid4().hex[:8]}"})
        response.raise_for_status()
        project = response.json()["id"]
        response = client.post("/api/conversations", json={"title": "容量长会话验收", "project_id": project, "model_name": model})
        response.raise_for_status()
        cid = response.json()["id"]
        response = client.post("/api/memories/remember", json={"title": "容量验收暗号", "content": "容量验收暗号为 CedarCapN42。",
                                                            "memory_type": "fact", "project_id": project})
        response.raise_for_status()
        memory = response.json()["id"]
        with SessionLocal() as db:
            now = datetime.now(timezone.utc) - timedelta(hours=2)
            ids = [str(uuid4()) for _ in range(1000)]
            db.execute(insert(Message), [dict(id=mid, conversation_id=cid, sequence=i+1,
                role="user" if i % 2 == 0 else "assistant", content=f"合成历史 {i}，仅用于容量测试。",
                status="done", created_at=now + timedelta(seconds=i)) for i, mid in enumerate(ids)])
            conv = db.get(Conversation, cid)
            conv.context_summary, conv.context_summary_boundary_message_id = "之前讨论容量测试。", ids[975]
            # 合成历史不应触发真实记忆 Worker 的积压提取。
            conv.memory_extraction_cursor = 1000
            db.commit()

        question = "容量验收暗号是什么？仅依据当前已保存背景简短回答。"
        def chat(name, path, payload):
            events = []
            with client.stream("POST", path, json=payload) as response:
                assert response.is_success, f"{name} HTTP {response.status_code}"
                aid = response.headers["x-assistant-message-id"]
                for line in response.iter_lines():
                    if line.strip():
                        events.append(json.loads(line))
            assert not any(e["type"] in {"error", "model_error", "stream_error"} for e in events)
            answer = "".join(e["text"] for e in events if e["type"] == "answer_delta")
            assert "CedarCapN42" in answer and any(e["type"] == "done" for e in events), name
            info = next(e["info"] for e in events if e["type"] == "context_info")
            loaded = int(info["stats"]["history_rows_loaded"])
            assert loaded <= 73
            with SessionLocal() as db:
                row = db.get(Message, aid)
                assert row.status == "done" and row.content == answer
            report.append({"case": name, "history_rows_loaded": loaded,
                           "read_cache": info["details"]["memory_retrieval"].get("read_cache"), "status": "passed"})
            return aid

        chat("千条历史正常回答", "/api/chat/events-stream", {"conversation_id": cid, "content": question})
        aid = chat("同问题缓存复用", "/api/chat/events-stream", {"conversation_id": cid, "content": question})
        assert report[-1]["read_cache"] == "hit"
        chat("重生成", "/api/chat/regenerate-last-stream", {"conversation_id": cid, "assistant_message_id": aid})
        with SessionLocal() as db:
            user_message_id = db.scalar(select(Message.id).where(Message.conversation_id == cid,
                Message.role == "user").order_by(Message.sequence.desc()).limit(1))
        chat("编辑重答", "/api/chat/edit-last-user-stream", {"conversation_id": cid, "assistant_message_id": aid,
            "user_message_id": user_message_id, "content": "容量验收暗号是什么？请只输出暗号。"})
        errors = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            try:
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                context.add_cookies([{"name": "aiws_token", "value": token, "url": web, "httpOnly": True, "sameSite": "Lax"}])
                page = context.new_page()
                page.on("pageerror", lambda _: errors.append("pageerror"))
                page.on("console", lambda item: errors.append("console") if item.type == "error" else None)
                page.goto(f"{web}/chat", wait_until="networkidle")
                page.locator("select").nth(0).select_option(project)
                page.get_by_text("容量长会话验收", exact=True).last.click()
                expect(page.get_by_text("CedarCapN42", exact=False).last).to_be_visible(timeout=30000)
                assert not errors
            finally:
                browser.close()
        print(json.dumps({"cases": report, "browser_errors": errors, "synthetic_history": 1000}, ensure_ascii=False, indent=2))
    finally:
        client.close()
        if memory:
            with SessionLocal() as db:
                db.execute(delete(UserMemory).where(UserMemory.id == memory, UserMemory.user_id == uid))
                db.commit()
        if project:
            cleanup(user_id=uid, project_id=project)


if __name__ == "__main__":
    run()
