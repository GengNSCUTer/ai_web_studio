from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.conversation_share import ConversationShare


class ConversationShareRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_conversation(self, conversation_id: str, user_id: str) -> ConversationShare | None:
        stmt = (
            select(ConversationShare)
            .where(
                ConversationShare.conversation_id == conversation_id,
                ConversationShare.user_id == user_id,
            )
            .order_by(ConversationShare.created_at.desc())
            .limit(1)
        )
        return self.db.scalars(stmt).first()

    def get_by_token(self, token: str) -> ConversationShare | None:
        stmt = select(ConversationShare).where(ConversationShare.token == token).limit(1)
        return self.db.scalars(stmt).first()

    def save(self, share: ConversationShare) -> ConversationShare:
        self.db.add(share)
        self.db.commit()
        self.db.refresh(share)
        return share

    def revoke(self, share: ConversationShare) -> ConversationShare:
        share.is_enabled = False
        share.revoked_at = datetime.now(timezone.utc)
        return self.save(share)
