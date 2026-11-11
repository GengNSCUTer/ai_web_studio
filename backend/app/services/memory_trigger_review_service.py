"""只读决策查询与拥有者标注；不复制消息正文，不改变记忆或任务。"""

from datetime import datetime, timezone

from sqlalchemy import select

from app.models.conversation import Conversation
from app.models.memory_trigger_decision import MemoryTriggerDecision


def owned_decisions(user_id: str):
    return select(MemoryTriggerDecision).join(Conversation,
        Conversation.id == MemoryTriggerDecision.conversation_id).where(
            MemoryTriggerDecision.user_id == user_id, Conversation.user_id == user_id,
            # 会话移到另一项目后，不把旧范围观测误认为当前范围证据。
            MemoryTriggerDecision.project_id.is_not_distinct_from(Conversation.project_id))


def label_decision(db, user_id: str, decision_id: str, payload):
    row = db.scalar(owned_decisions(user_id).where(MemoryTriggerDecision.id == decision_id)
        .with_for_update(of=MemoryTriggerDecision))
    if row is None:
        raise LookupError("Trigger decision not found")
    row.expected_action = "extract_now" if payload.expected_action == "extract" else payload.expected_action
    row.review_reason = payload.review_reason
    row.reviewed_at = datetime.now(timezone.utc)
    db.commit()
    return row
