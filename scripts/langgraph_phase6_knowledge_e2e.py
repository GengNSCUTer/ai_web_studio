"""固定账号真实上传→Outbox/Redis→独立Worker图→在线Embedding→RAG回答→浏览器验收。"""

import argparse
import json
from chat_sse_client import chat_response, sse_events
from pathlib import Path
import shutil
import sys
import time
from uuid import uuid4

import httpx
from sqlalchemy import delete, select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def run(output):
    from playwright.sync_api import sync_playwright, expect
    from stage46a_real_e2e import prepare_fixture, cleanup
    from app.core.config import settings
    from app.core.database import SessionLocal
    from app.models.knowledge import KnowledgeBase, KnowledgeJob, KnowledgeIndexGeneration, KnowledgeChunk, KnowledgeRetrievalLog
    from app.models.message import Message

    token, uid, pid, _, _ = prepare_fixture()
    client = httpx.Client(base_url="http://127.0.0.1:32007", headers={"Authorization": f"Bearer {token}"}, timeout=180)
    report = {"scope": "临时知识库；真实上传、PG/Outbox/Redis/Worker、在线bge-m3/DeepSeek-V3.2、浏览器"}
    kid = None
    upload_path = None
    errors = []

    def request(method, path, **kwargs):
        response = client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json() if response.content else None

    def wait(job_id):
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            jobs = request("GET", f"/api/knowledge-bases/{kid}/jobs")
            job = next(row for row in jobs if row["id"] == job_id)
            if job["status"] in {"succeeded", "failed", "dead_letter"}:
                assert job["status"] == "succeeded", f"真实Job失败：{job.get('error_code')}"
                return job
            time.sleep(0.4)
        raise AssertionError("真实独立知识库Worker未完成Job")

    try:
        name = f"LangGraph6临时知识库-{uuid4().hex[:8]}"
        kb = request("POST", "/api/knowledge-bases", json={"name": name, "project_id": pid,
            "parser_provider": "local_basic", "embedding_provider": "siliconflow", "embedding_model": "BAAI/bge-m3",
            "embedding_dimensions": 1024, "rerank_enabled": False, "retrieval_mode": "hybrid", "score_threshold": 0})
        kid = kb["id"]
        content = "# 蓝鲸知识库验收\n\n蓝鲸项目数据库使用PostgreSQL，队列使用Redis Stream。\n备份默认保留17天，验收暗号是CedarGraph61。\n"
        uploaded = request("POST", "/api/uploads", files={"files": ("图索引验收.md", content.encode(), "text/markdown")})[0]
        upload_path = Path(settings.upload_dir) / uploaded["storage_key"]
        doc = request("POST", f"/api/knowledge-bases/{kid}/documents", json={k: uploaded[k] for k in (
            "file_name", "mime_type", "file_size", "storage_key")})
        did = doc["id"]
        parsed = request("POST", f"/api/knowledge-bases/{kid}/documents/{did}/parse")
        parse_job = wait(parsed["job"]["id"])
        indexing = request("POST", f"/api/knowledge-bases/{kid}/documents/{did}/index")
        index_job = wait(indexing["job"]["id"])
        duplicate = request("POST", f"/api/knowledge-bases/{kid}/documents/{did}/index")
        assert duplicate["job"]["id"] == index_job["id"]
        with SessionLocal() as db:
            kb_row = db.get(KnowledgeBase, kid)
            gen = db.get(KnowledgeIndexGeneration, kb_row.active_index_generation)
            assert gen and gen.status == "active" and gen.manifest_hash
            rows = list(db.scalars(select(KnowledgeChunk).where(KnowledgeChunk.knowledge_base_id == kid,
                KnowledgeChunk.index_generation == gen.id)))
            assert rows and all(row.embedding is not None and len(row.embedding) == 1024 for row in rows)
            report.update(parse_status=parse_job["status"], index_status=index_job["status"], active_generation=gen.status,
                          chunk_count=len(rows), duplicate_index_reused_job=True)
        conv = request("POST", "/api/conversations", json={"project_id": pid, "title": "图索引最终问答",
                                                            "model_name": "deepseek-ai/DeepSeek-V3.2"})
        response = chat_response(client, "/api/chat/runs", json={"conversation_id": conv["id"], "knowledge_base_id": kid,
            "content": "根据蓝鲸资料库，备份保留几天，验收暗号是什么？简短回答并引用知识库来源。"})
        response.raise_for_status()
        events = sse_events(response.text)
        answer = "".join(event["text"] for event in events if event["type"] == "answer_delta")
        assert events[-1]["type"] == "done" and "17" in answer and "CedarGraph61" in answer and "[KB" in answer
        with SessionLocal() as db:
            message = db.get(Message, events[-1]["assistant_message_id"])
            assert message.status == "done" and message.content == answer
            log = db.scalar(select(KnowledgeRetrievalLog).where(KnowledgeRetrievalLog.assistant_message_id == message.id))
            assert log and log.status == "success" and json.loads(log.selected_json)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context(viewport={"width": 1440, "height": 1000})
                context.add_cookies([{"name": "aiws_token", "value": token, "url": "http://127.0.0.1:32008",
                                     "httpOnly": True, "sameSite": "Lax"}])
                page = context.new_page()
                page.on("pageerror", lambda _: errors.append("pageerror"))
                page.on("console", lambda m: errors.append("console") if m.type == "error" else None)
                page.goto("http://127.0.0.1:32008/knowledge", wait_until="networkidle")
                page.get_by_text(name, exact=True).first.click()
                expect(page.get_by_text("图索引验收.md", exact=True).first).to_be_visible(timeout=20000)
                page.goto("http://127.0.0.1:32008/chat", wait_until="networkidle")
                page.locator("select").nth(0).select_option(pid)
                page.get_by_text("图索引最终问答", exact=True).last.click()
                expect(page.get_by_text("CedarGraph61", exact=False).first).to_be_visible(timeout=20000)
                assert not errors
            finally:
                browser.close()
        report.update(final_answer_facts_and_citation=True, persisted_answer=True, browser_errors=errors, passed=True)
    except Exception as exc:
        report.update(passed=False, error_type=type(exc).__name__)
        raise
    finally:
        if kid:
            # 只清理本次知识库日志/世代/任务及原始文件，其他固定库不动。
            request("DELETE", f"/api/knowledge-bases/{kid}")
            shutil.rmtree(Path(settings.upload_dir) / uid / "knowledge" / kid, ignore_errors=True)
        if upload_path:
            upload_path.unlink(missing_ok=True)
        cleanup(user_id=uid, project_id=pid)
        client.close()
        report["temporary_data_cleaned"] = True
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.output), ensure_ascii=False, indent=2))
