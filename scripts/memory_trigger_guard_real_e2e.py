"""在线验证混合输入提取与关闭后的零触发观测；恢复设置并清理临时项目。"""

import json
import os
from pathlib import Path
import sys
import time

import httpx
from sqlalchemy import delete, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.core.database import SessionLocal
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.memory_trigger_decision import MemoryTriggerDecision
from app.models.user_memory import MemoryExtractionJob, UserMemory
from app.models.user_setting import UserSetting
from chat_sse_client import chat_stream, iter_sse_events
from stage46a_real_e2e import cleanup, prepare_fixture


def run():
    token, user_id, project, _, _ = prepare_fixture()
    fields = ("memory_enabled", "memory_auto_candidate_enabled", "memory_auto_activate_enabled",
              "memory_semantic_trigger_enabled")
    with SessionLocal() as db:
        setting = db.scalar(select(UserSetting).where(UserSetting.user_id == user_id))
        original = {field: getattr(setting, field) for field in fields}
        model = setting.default_model
    client = httpx.Client(base_url=os.getenv("AIWS_E2E_BACKEND_URL", "http://127.0.0.1:32007"),
        headers={"authorization": f"Bearer {token}"}, timeout=240)
    cases = []

    def request(method, path, **kwargs):
        response = client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()

    def chat(conv, text):
        events = []
        with chat_stream(client, json={"conversation_id": conv, "content": text, "model_name": model}) as response:
            events = list(iter_sse_events(response.iter_lines()))
        assert any(event["type"] == "done" for event in events), "模型未正常完成"
        assert not any(event["type"] == "model_error" for event in events), "真实模型失败"
        with SessionLocal() as db:
            assistant = db.scalar(select(Message).where(Message.conversation_id == conv,
                Message.role == "assistant").order_by(Message.sequence.desc()).limit(1))
            assert assistant.status == "done"
            assistant_id, sequence = assistant.id, assistant.sequence
        # Chat终态提交后才派发记忆，需要等待明确的扫描位置而不是固定睡眠猜测。
        end = time.monotonic() + 30
        while time.monotonic() < end:
            with SessionLocal() as db:
                if (db.get(Conversation, conv).memory_scan_cursor or 0) >= sequence - 1:
                    return assistant_id
            time.sleep(.2)
        raise AssertionError("未完成记忆开关/触发分支")

    def completed_job(conv):
        end = time.monotonic() + 120
        while time.monotonic() < end:
            with SessionLocal() as db:
                job = db.scalar(select(MemoryExtractionJob).where(MemoryExtractionJob.conversation_id == conv)
                    .order_by(MemoryExtractionJob.created_at.desc()).limit(1))
                if job and job.status == "failed":
                    raise AssertionError(f"记忆提取失败：{job.error_code}")
                if job and job.status == "succeeded":
                    return job
            time.sleep(.3)
        raise AssertionError("记忆Worker未完成")

    try:
        request("PATCH", "/api/settings", json=dict(memory_enabled=True, memory_auto_candidate_enabled=True,
            memory_auto_activate_enabled=False, memory_semantic_trigger_enabled=False))
        conv = request("POST", "/api/conversations", json={"project_id": project, "model_name": model,
            "title": "混合陈述与关闭边界验收"})["id"]
        chat(conv, "我的项目使用 PostgreSQL，问题是如何优化索引？")
        job = completed_job(conv)
        with SessionLocal() as db:
            memories = list(db.scalars(select(UserMemory).where(UserMemory.extraction_job_id == job.id)))
            assert memories and any("PostgreSQL" in row.content for row in memories)
            assert all(row.status == "pending" and row.evidence_quote for row in memories)
            assert job.trigger_reason == "stable_statement_with_question" and not job.semantic_model_called
            before_decisions = set(db.scalars(select(MemoryTriggerDecision.id).where(MemoryTriggerDecision.conversation_id == conv)))
            before_jobs = set(db.scalars(select(MemoryExtractionJob.id).where(MemoryExtractionJob.conversation_id == conv)))
        cases.append({"case": "混合输入首轮入队、在线提取及原文核验，不自动启用", "status": "passed",
            "pending_candidates": len(memories), "additional_semantic_calls": 0})

        request("PATCH", "/api/settings", json={"memory_enabled": False})
        disabled_assistant = chat(conv, "请记住：我的项目采用 Redis。")
        with SessionLocal() as db:
            disabled = db.scalar(select(Message.id).where(Message.conversation_id == conv,
                Message.role == "user").order_by(Message.sequence.desc()).limit(1))
            assert set(db.scalars(select(MemoryTriggerDecision.id).where(MemoryTriggerDecision.conversation_id == conv))) == before_decisions
            assert set(db.scalars(select(MemoryExtractionJob.id).where(MemoryExtractionJob.conversation_id == conv))) == before_jobs
            conversation = db.get(Conversation, conv)
            assert conversation.memory_scan_cursor == db.get(Message, disabled_assistant).sequence
            assert json.loads(conversation.memory_trigger_state)["pending"] == []
        cases.append({"case": "关闭记忆后真实Chat正常，零新增触发观测/提取任务", "status": "passed"})

        request("PATCH", "/api/settings", json={"memory_enabled": True})
        chat(conv, "我的项目使用 PostgreSQL。")
        latest = completed_job(conv)
        assert latest.id != job.id and disabled not in latest.source_message_ids.split(",")
        cases.append({"case": "重新开启处理新输入，不补记关闭期间内容", "status": "passed"})
        report = {"status": "passed", "model": model, "chat_turns": 3, "cases": cases,
            "scope": "固定账号在线模型、真实SSE/ChatWorker/MemoryWorker/PostgreSQL；非自然用户标注集"}
        output = Path(os.getenv("AIWS_MEMORY_GUARD_REPORT", "/tmp/aiws-memory-trigger-guard-real.json"))
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))
    finally:
        try:
            request("PATCH", "/api/settings", json=original)
        finally:
            client.close()
            with SessionLocal() as db:
                conversations = list(db.scalars(select(Conversation.id).where(Conversation.project_id == project)))
                db.execute(delete(UserMemory).where(UserMemory.user_id == user_id, UserMemory.project_id == project))
                db.execute(delete(MemoryExtractionJob).where(MemoryExtractionJob.conversation_id.in_(conversations)))
                db.commit()
            cleanup(user_id=user_id, project_id=project)


if __name__ == "__main__":
    run()
