"""用提取时的消息版本核对长期记忆来源，不复制原文或创建另一份版本。"""

import json

from sqlalchemy import func, select

from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user_memory import MemoryExtractionJob
from app.services.memory_extraction_policy import source_digest


def source_bound(memory):
    return bool(getattr(memory, "extraction_job_id", None) or
                getattr(memory, "source_message_ids", None) or
                getattr(memory, "source", None) in {"auto_candidate", "auto_confirmed"})


def source_message_ids(memory):
    return set(filter(None, (memory.source_message_ids or "").split(",")))


def source_issues(db, memories, *, lock=False):
    """只查传入记录所引用的消息。缓存里的记录也要过这一步。"""
    linked = [row for row in memories if source_bound(row)]
    if not linked:
        return {}
    issues = {}
    if db is None:
        return {row.id: "snapshot_missing" for row in linked}
    postgres = db.get_bind().dialect.name == "postgresql"
    for start in range(0, len(linked), 200):
        batch = linked[start:start + 200]
        job_query = select(
            MemoryExtractionJob.id, MemoryExtractionJob.user_id, MemoryExtractionJob.project_id,
            MemoryExtractionJob.conversation_id, MemoryExtractionJob.source_snapshot, MemoryExtractionJob.error_code,
        ).where(MemoryExtractionJob.id.in_({row.extraction_job_id for row in batch if row.extraction_job_id}),
                MemoryExtractionJob.user_id.in_({row.user_id for row in batch}))
        jobs = {row.id: row for row in db.execute(job_query)}
        # 在线只带回摘要和归属，避免缓存命中时仍把用户原话加载回应用进程。
        message_fields = (func.encode(func.sha256(func.convert_to(
            func.concat(Message.generation_id, "\n", Message.content), "UTF8")), "hex").label("digest"),
        ) if postgres else (Message.generation_id, Message.content)
        query = select(Message.id, Message.conversation_id, *message_fields,
                       Conversation.user_id, Conversation.project_id).join(Conversation).where(
            Message.id.in_(set().union(*(source_message_ids(row) for row in batch))), Message.role == "user",
            Conversation.user_id.in_({row.user_id for row in batch}),
        )
        if lock and postgres:
            # 会话迁移可能持有会话锁并等待用户锁，立即拒绝竞争中的确认以保持锁顺序。
            query = query.with_for_update(of=(Message, Conversation), read=True, nowait=True)
        messages = {row.id: row for row in db.execute(query)}
        for row in batch:
            job = jobs.get(row.extraction_job_id)
            ids = source_message_ids(row)
            if not ids or not row.source_conversation_id:
                issues[row.id] = "snapshot_missing"
                continue
            if not ids <= messages.keys():
                issues[row.id] = "source_missing"
                continue
            if any(messages[key].project_id != row.project_id or messages[key].user_id != row.user_id or
                   messages[key].conversation_id != row.source_conversation_id for key in ids):
                issues[row.id] = "scope_changed"
                continue
            if not job:
                issues[row.id] = "snapshot_missing"
                continue
            try:
                snapshot = json.loads(job.source_snapshot or "null")
            except (ValueError, TypeError):
                snapshot = None
            if job.error_code == "conversation_scope_changed":
                issues[row.id] = "scope_changed"
            elif not isinstance(snapshot, dict) or not ids <= snapshot.keys():
                issues[row.id] = "snapshot_missing"
            elif (job.user_id != row.user_id or job.project_id != row.project_id or
                  job.conversation_id != row.source_conversation_id):
                issues[row.id] = "scope_changed"
            elif any((messages[key].digest if postgres else source_digest(messages[key])) != snapshot[key] for key in ids):
                issues[row.id] = "source_changed"
    return issues
