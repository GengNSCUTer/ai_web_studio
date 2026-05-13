from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.conversation import Conversation


class ConversationRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_by_user(self, user_id: str) -> list[Conversation]:
        stmt = (
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(
                Conversation.is_pinned.desc(),
                Conversation.updated_at.desc(),
                Conversation.created_at.desc(),
            )
        )
        return list(self.db.scalars(stmt).all())

    def get_by_user(self, conversation_id: str, user_id: str) -> Conversation | None:
        stmt = (
            select(Conversation)
            .where(Conversation.id == conversation_id, Conversation.user_id == user_id)
            .limit(1)
        )
        return self.db.scalars(stmt).first()

    def create(self, conversation: Conversation) -> Conversation:
        self.db.add(conversation)
        self.db.commit()
        self.db.refresh(conversation)
        return conversation

    def save(self, conversation: Conversation) -> Conversation:
        self.db.add(conversation)
        self.db.commit()
        self.db.refresh(conversation)
        return conversation

    def update_context_summary(
        self,
        conversation_id: str,
        *,
        context_summary: str | None,
        context_summary_boundary_message_id: str | None,
    ) -> Conversation | None:
        conversation = self.db.get(Conversation, conversation_id)
        if not conversation:
            return None

        conversation.context_summary = context_summary
        conversation.context_summary_boundary_message_id = context_summary_boundary_message_id
        self.db.add(conversation)
        self.db.commit()
        self.db.refresh(conversation)
        return conversation

    def touch(self, conversation_id: str) -> None:
        stmt = (
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(updated_at=func.now())
        )
        self.db.execute(stmt)
        self.db.commit()

    def delete(self, conversation: Conversation) -> None:
        self.db.delete(conversation)
        self.db.commit()
