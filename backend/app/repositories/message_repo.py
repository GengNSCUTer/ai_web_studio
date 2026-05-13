from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.message import Message


class MessageRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_by_conversation(self, conversation_id: str) -> list[Message]:
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc())
        )
        return list(self.db.scalars(stmt).all())

    def create(self, message: Message) -> Message:
        self.db.add(message)
        self.db.commit()
        self.db.refresh(message)
        return message

    def save(self, message: Message) -> Message:
        self.db.add(message)
        self.db.commit()
        self.db.refresh(message)
        return message

    def get_by_id_and_conversation(self, message_id: str, conversation_id: str) -> Message | None:
        stmt = (
            select(Message)
            .where(Message.id == message_id, Message.conversation_id == conversation_id)
            .limit(1)
        )
        return self.db.scalars(stmt).first()

    def mark_stale_streaming_messages(self, conversation_id: str) -> int:
        stale_before = datetime.now(timezone.utc) - timedelta(minutes=15)
        stmt = select(Message).where(
            Message.conversation_id == conversation_id,
            Message.role == "assistant",
            Message.status == "streaming",
        )
        messages = list(self.db.scalars(stmt).all())
        if not messages:
            return 0

        stale_messages: list[Message] = []
        for message in messages:
            reference_time = message.updated_at or message.created_at
            if reference_time.tzinfo is None:
                reference_time = reference_time.replace(tzinfo=timezone.utc)
            if reference_time > stale_before:
                continue

            message.status = "failed"
            if not (message.content or "").strip():
                message.content = ""
            stale_messages.append(message)

        if not stale_messages:
            return 0

        self.db.commit()
        return len(stale_messages)

    def delete(self, message: Message) -> None:
        self.db.delete(message)
        self.db.commit()

    def bulk_delete(self, conversation_id: str, message_ids: list[str]) -> int:
        if not message_ids:
            return 0

        stmt = select(Message).where(
            Message.conversation_id == conversation_id,
            Message.id.in_(message_ids),
        )
        messages = list(self.db.scalars(stmt).all())
        for message in messages:
            self.db.delete(message)
        self.db.commit()
        return len(messages)

    def delete_by_conversation(self, conversation_id: str) -> int:
        stmt = select(Message).where(Message.conversation_id == conversation_id)
        messages = list(self.db.scalars(stmt).all())
        for message in messages:
            self.db.delete(message)
        self.db.commit()
        return len(messages)
