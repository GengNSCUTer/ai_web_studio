"""真实浏览器选择现有中文库，验证Hybrid故障降级仍可生成带引用的完整答案。"""

import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from sqlalchemy import select
from app.core.database import SessionLocal
from app.models.knowledge import KnowledgeBase, KnowledgeRetrievalLog
from app.models.conversation import Conversation
from app.models.message import Message
from stage46a_real_e2e import prepare_fixture, cleanup


def main():
    from playwright.sync_api import sync_playwright, expect
    token, user_id, project_id, _, _ = prepare_fixture()
    errors = []
    try:
        with SessionLocal() as db:
            kb = db.scalar(select(KnowledgeBase).where(KnowledgeBase.user_id == user_id,
                KnowledgeBase.name == "中文后端基础资料库（顺序修复版）"))
            assert kb is not None and kb.retrieval_mode == "hybrid"
            kb_id, kb_name = kb.id, kb.name
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            context.add_cookies([{"name": "aiws_token", "value": token, "url": "http://127.0.0.1:32008",
                "httpOnly": True, "sameSite": "Lax"}])
            page = context.new_page()
            page.on("pageerror", lambda _: errors.append("pageerror"))
            page.on("console", lambda m: errors.append("console") if m.type == "error" else None)
            page.goto("http://127.0.0.1:32008/chat", wait_until="networkidle")
            expect(page.get_by_placeholder("输入你的问题")).to_be_visible()
            page.locator("select").nth(0).select_option(project_id)
            page.get_by_role("button", name="新对话", exact=True).click()
            page.get_by_role("button", name="知识库", exact=True).click()
            page.get_by_label(kb_name, exact=True).check()
            composer = page.get_by_placeholder("输入你的问题")
            composer.click()
            composer.fill("根据知识库，如何使用Redis List模拟阻塞队列？请列出两个阻塞出队命令并给出资料引用。")
            page.get_by_role("button", name="发送消息", exact=True).click()
            status = page.locator("article.message-assistant").last.locator("div").first.get_by_text(
                re.compile(r"^(done|failed|cancelled)$", re.IGNORECASE))
            expect(status).to_be_visible(timeout=240000)
            expect(status).to_have_text("done")
            page.screenshot(path="/tmp/aiws-hybrid-chat-20261116.png", full_page=True)
            browser.close()
        with SessionLocal() as db:
            conv = db.scalar(select(Conversation).where(Conversation.project_id == project_id).order_by(Conversation.created_at.desc()))
            answer = db.scalar(select(Message).where(Message.conversation_id == conv.id,
                Message.role == "assistant").order_by(Message.sequence.desc()))
            log = db.scalar(select(KnowledgeRetrievalLog).where(KnowledgeRetrievalLog.conversation_id == conv.id,
                KnowledgeRetrievalLog.knowledge_base_id == kb_id).order_by(KnowledgeRetrievalLog.created_at.desc()))
            assert answer.status == "done" and log is not None
            candidates = json.loads(log.candidates_json)
            sources = json.loads(answer.external_sources or "[]")
            assert candidates and sources and re.search(r"\[KB\d+\]", answer.content)
            assert all(term in answer.content.upper() for term in ("BLPOP", "BRPOP"))
            assert not errors
            print(json.dumps({"scope": "真实浏览器、ChatWorker、在线最终模型、现有中文库PG与BM25；不变更账号设置或库配置",
                "status": "passed", "assistant_status": answer.status, "answer_chars": len(answer.content),
                "browser_errors": len(errors), "retrieval_candidates": len(candidates), "sources": len(sources),
                "hybrid_fallback": any(r["metadata"].get("hybrid_fallback") for r in candidates),
                "vector_error_codes": sorted({r["metadata"].get("vector_error_code") for r in candidates
                    if r["metadata"].get("vector_error_code")}), "citations_and_two_blocking_commands": True}, ensure_ascii=False))
    finally:
        cleanup(user_id=user_id, project_id=project_id)


if __name__ == "__main__":
    main()
