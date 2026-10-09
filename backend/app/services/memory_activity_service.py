"""从已提交任务及当前记忆状态投影通知，不把模型承诺当作保存成功。"""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user_memory import MemoryExtractionJob, UserMemory
from app.repositories.conversation_repo import ConversationRepository
from app.schemas.memory import MemoryActivityItem, MemoryActivityResponse
from app.services.memory_policy import contains_credential


class MemoryActivityService:
    def __init__(self, db: Session):
        self.db = db

    def get_activity(self, user_id: str, conversation_id: str) -> MemoryActivityResponse:
        conversation = ConversationRepository(self.db).get_by_user(conversation_id, user_id)
        if conversation is None:
            raise LookupError("Conversation not found")
        scope = [MemoryExtractionJob.user_id == user_id,
                 MemoryExtractionJob.conversation_id == conversation_id,
                 MemoryExtractionJob.project_id == conversation.project_id]
        pending = self.db.scalar(select(MemoryExtractionJob.id).where(
            *scope, MemoryExtractionJob.status.in_(["pending", "running"])).limit(1))
        recent_jobs = select(MemoryExtractionJob.id).where(*scope).order_by(
            MemoryExtractionJob.created_at.desc(), MemoryExtractionJob.id.desc()).limit(20)
        # 只暴露任务提交成功后的结果；撤销/过期/更正始终读取当前状态。
        rows = self.db.scalars(select(UserMemory).join(
            MemoryExtractionJob, UserMemory.extraction_job_id == MemoryExtractionJob.id
        ).where(*scope, MemoryExtractionJob.id.in_(recent_jobs),
            MemoryExtractionJob.status == "succeeded", UserMemory.user_id == user_id,
            UserMemory.source_conversation_id == conversation_id,
            UserMemory.project_id == conversation.project_id
        ).order_by(UserMemory.created_at.desc(), UserMemory.id.desc()).limit(160)).all()
        now = datetime.now(timezone.utc)
        items = []
        for row in rows:
            if contains_credential(row.title, row.content):
                continue
            state = row.status
            expiry = row.expires_at
            if expiry and (expiry.replace(tzinfo=timezone.utc) if expiry.tzinfo is None else expiry) <= now:
                if state in {"active", "pending"}:
                    state = "expired"
            if state == "active" and not row.is_enabled:
                state = "disabled"
            sensitive = row.sensitivity == "sensitive"
            items.append(MemoryActivityItem(
                # 撤销/审核会增加版本，但仍是同一条通知，不能重新弹出一条。
                event_id=f"memory:{row.id}", memory_id=row.id,
                job_id=row.extraction_job_id, title="待审核记忆" if sensitive else row.title,
                content="" if sensitive else row.content, status=state,
                version=row.version, created_at=row.created_at))
        return MemoryActivityResponse(conversation_id=conversation_id,
                                      has_pending_jobs=pending is not None, items=items)
