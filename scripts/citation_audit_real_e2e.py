"""真实在线Chat、工具、浏览器审计与刷新；另用明确标记的夹具检查警告展示。"""

import json
from pathlib import Path

from playwright.sync_api import sync_playwright, expect
from sqlalchemy import select

from stage46a_real_e2e import prepare_fixture, collect_result, cleanup, BASE_URL, MODEL_TIMEOUT_MS
from app.core.database import SessionLocal
from app.models.message import Message
from app.models.conversation import Conversation
from app.models.knowledge import KnowledgeRetrievalLog
from app.repositories.message_repo import MessageRepository
from app.services.citation_audit_service import audit_answer_citations


def run():
    token, uid, pid, fid, _ = prepare_fixture()
    errors, result = [], {}
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(viewport={"width": 1440, "height": 1000})
            context.add_cookies([{"name": "aiws_token", "value": token, "url": BASE_URL, "httpOnly": True, "sameSite": "Lax"}])
            response = context.request.post(BASE_URL + "/api/backend/tools/skills/workspace.document-review/upgrade")
            assert response.ok, "审核Skill升级失败"
            page = context.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.goto(BASE_URL + "/chat", wait_until="networkidle")
            expect(page.get_by_placeholder("输入你的问题")).to_be_visible()
            page.locator("select").nth(0).select_option(pid)
            page.get_by_role("button", name="新对话", exact=True).click()
            page.get_by_test_id("tool-run-mode-selector").select_option("workspace_review")
            page.get_by_test_id("skill-selector").click()
            page.get_by_test_id("skill-option-workspace.document-review").click()
            page.get_by_placeholder("输入你的问题").fill("在当前工作区查找并读取 stage46a-readme.md，告诉我文件用途和第一行标题。"
                "请明确写出文件名stage46a-readme.md，并原样引用第一行（保留#）。"
                "只用工作区文件工具，先搜索再读取，不联网不修改。请在结论后引用本轮来源编号。")
            page.get_by_role("button", name="发送消息", exact=True).click()
            page.wait_for_function("() => document.querySelector('button[type=submit]')?.disabled === true", timeout=10000)
            page.wait_for_function("() => ['发送消息','Send'].includes(document.querySelector('button[type=submit]')?.textContent?.trim())", timeout=MODEL_TIMEOUT_MS)
            collected = collect_result(user_id=uid, project_id=pid, file_id=fid)
            with SessionLocal() as db:
                answer = db.scalar(select(Message).where(Message.conversation_id == collected["conversation_id"],
                    Message.role == "assistant").order_by(Message.created_at.desc()))
                audit = json.loads(answer.citation_audit)
                stored_sources = json.loads(answer.external_sources)
                assert {item["citation_label"] for item in stored_sources} == set(audit["available_labels"])
                assert all(item["used_in_prompt"] and item["metadata"]["citation_body_chars"] == len(item["display_text"])
                           for item in stored_sources)
                assert audit["support_checked"] is False and audit["available_labels"], "未基于最终证据执行审计"
                assert audit["status"] in {"passed", "needs_review"}
                notice = "引用标签有效 · 未核验证据是否支持结论" if audit["status"] == "passed" else "引用需要复核，原回答已保留。"
                cid, mid = answer.conversation_id, answer.id
            expect(page.get_by_text(notice, exact=True)).to_be_visible(timeout=15000)
            page.goto(BASE_URL + f"/chat?conversation={cid}", wait_until="networkidle")
            expect(page.get_by_text(notice, exact=True)).to_be_visible()
            links = page.locator('a[href^="#evidence-"]')
            assert links.count() > 0, "真实工具回答未产生可跳转的引用"
            target_id = links.first.get_attribute("href")[1:]
            links.first.click()
            expect(page.locator(f'[id="{target_id}"]')).to_be_visible()
            result.update({"online_chat_status": collected["assistant_status"], "citation_status": audit["status"],
                "available_labels": audit["available_labels"], "support_checked": False, "refresh_keeps_audit": True})
            # 复用现有论文库的一道问题，只新增临时会话/日志，不更改题集或知识库。
            fixture = json.loads((Path(__file__).resolve().parents[1] / "backend/evals/paper_100_chinese_20261028.json").read_text())
            with SessionLocal() as db:
                rag_conversation = Conversation(user_id=uid, project_id=pid, title="引用审计RAG验收", model_name="deepseek-ai/DeepSeek-V3.2")
                db.add(rag_conversation); db.commit(); rag_cid = rag_conversation.id
            response = context.request.post(BASE_URL + "/api/backend/chat/events-stream", data={
                "conversation_id": rag_cid, "content": fixture["cases"][0]["query"],
                "knowledge_base_id": fixture["knowledge_base_id"], "web_search_enabled": False,
                "model_name": "deepseek-ai/DeepSeek-V3.2"}, timeout=MODEL_TIMEOUT_MS)
            assert response.ok, "真实RAG请求未成功"
            events = [json.loads(line) for line in response.text().splitlines() if line.strip()]
            rag_audit = next(item["audit"] for item in events if item["type"] == "citation_audit")
            assert events[-1]["type"] == "done" and any(label.startswith("[KB") for label in rag_audit["available_labels"])
            with SessionLocal() as db:
                answer = db.scalar(select(Message).where(Message.conversation_id == rag_cid, Message.role == "assistant"))
                assert json.loads(answer.citation_audit) == rag_audit and answer.status == "done"
                stored_sources = json.loads(answer.external_sources)
                event_sources = next(item["sources"] for item in events if item["type"] == "tool_sources")
                assert stored_sources == event_sources, "实时来源和历史来源不一致"
                logs = db.scalars(select(KnowledgeRetrievalLog).where(KnowledgeRetrievalLog.assistant_message_id == answer.id)).all()
                assert logs, "没有保留真实检索日志"
                logged_sources = [item for log in logs for item in json.loads(log.sources_json or "[]")]
                assert {item["citation_label"] for item in logged_sources} == set(rag_audit["available_labels"])
                assert all(json.loads(log.candidates_json or "[]") for log in logs), "原始召回证据缺失"
                assert {item["citation_label"] for item in stored_sources} == set(rag_audit["available_labels"])
                assert all(item["used_in_prompt"] and item["metadata"]["citation_body_chars"] == len(item["display_text"])
                           for item in stored_sources)
            page.goto(BASE_URL + f"/chat?conversation={rag_cid}", wait_until="networkidle")
            rag_notice = "引用标签有效 · 未核验证据是否支持结论" if rag_audit["status"] == "passed" else "引用需要复核，原回答已保留。"
            expect(page.get_by_text(rag_notice, exact=True)).to_be_visible()
            links = page.locator('a[href^="#evidence-"]')
            assert links.count() > 0, "真实RAG回答没有来源跳转"
            links.first.click()
            expect(page.locator('[id^="evidence-"]').first).to_be_visible()
            result.update({"online_rag_citation_status": rag_audit["status"],
                           "online_rag_available_sources": len(rag_audit["available_labels"])})
            page.goto(BASE_URL + f"/chat?conversation={cid}", wait_until="networkidle")
            # 此警告是持久化展示夹具，不冒充真实模型产生的错误引用。
            with SessionLocal() as db:
                repo = MessageRepository(db)
                repo.create(Message(conversation_id=cid, role="user", content="引用警告展示测试（合成夹具）", status="done"))
                final_sources = [{"source_type": "knowledge", "provider": "synthetic", "title": f"结构化来源{i}",
                    "display_text": f"第{i}条实际保留的证据正文。", "citation_label": f"[KB{i}]", "used_in_prompt": True,
                    "metadata": {"citation_scope": "final_prompt", "citation_body_chars": len(f"第{i}条实际保留的证据正文。"),
                        "citation_body_truncated": i == 8}} for i in range(1, 9)]
                warning_text = ("展示夹具：错误引用[KB999]；保留来源[KB8]；已排除[KB9]。\n\n`代码示例[KB8]`"
                                "\n\n字面示例\\[KB7]，真实引用[KB7]；[KB6](https://wrong.example.test)。")
                warning = audit_answer_citations(warning_text, sources=final_sources, known_labels=["[KB9]"])
                warning_message = repo.create(Message(conversation_id=cid, role="assistant", content=warning_text,
                    external_sources=json.dumps(final_sources), citation_audit=json.dumps(warning), status="done"))
                warning_id = warning_message.id
                db.commit()
            page.reload(wait_until="networkidle")
            expect(page.get_by_text("不存在的来源编号：[KB999]", exact=True)).to_be_visible()
            expect(page.get_by_text("未进入最终上下文的来源：[KB9]", exact=True)).to_be_visible()
            expect(page.get_by_role("link", name="[KB999]", exact=True)).to_have_count(0)
            expect(page.get_by_role("link", name="[KB9]", exact=True)).to_have_count(0)
            expect(page.get_by_role("link", name="[KB8]", exact=True)).to_have_count(1)
            expect(page.get_by_role("link", name="[KB7]", exact=True)).to_have_count(1)
            expect(page.get_by_role("link", name="[KB6]", exact=True)).to_have_attribute("href", f"#evidence-{warning_id}-KB6")
            page.get_by_role("link", name="[KB8]", exact=True).click()
            expect(page.locator(f'#evidence-{warning_id}-KB8')).to_be_visible()
            expect(page.get_by_text("[KB8] 结构化来源8", exact=True)).to_be_visible()
            expect(page.get_by_text("第8条实际保留的证据正文。", exact=True)).to_be_visible()
            assert page.locator(f'[id^="evidence-{warning_id}-KB"]').count() == 8, "第7/8来源不应被前端截掉"
            page.screenshot(path="/tmp/aiws-citation-audit-desktop.png", full_page=True)
            page.set_viewport_size({"width": 390, "height": 844})
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth + 1")
            expect(page.get_by_text("不存在的来源编号：[KB999]", exact=True)).to_be_visible()
            page.screenshot(path="/tmp/aiws-citation-audit-mobile.png", full_page=True)
            with SessionLocal() as db:
                assert db.get(Message, warning_id).status == "done", "警告不得改变回答生成状态"
                assert db.get(Message, mid).citation_audit is not None
            assert not errors, errors[:3]
            result.update({"synthetic_warning_and_raw_answer_visible": True, "all_eight_sources_and_jump": True,
                           "unknown_and_excluded_not_clickable": True, "desktop_mobile_console_errors": len(errors)})
            context.close(); browser.close()
        print(json.dumps(result, ensure_ascii=False))
    finally:
        cleanup(user_id=uid, project_id=pid)


if __name__ == "__main__":
    run()
