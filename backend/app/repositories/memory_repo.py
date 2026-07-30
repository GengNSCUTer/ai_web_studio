from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.user_memory import UserMemory


class UserMemoryRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_by_user(self, user_id: str, *, enabled_only: bool = False) -> list[UserMemory]:
        stmt = select(UserMemory).where(UserMemory.user_id == user_id)
        if enabled_only:
            stmt = stmt.where(
                UserMemory.is_enabled.is_(True),
                UserMemory.status == "active",
                or_(UserMemory.expires_at.is_(None), UserMemory.expires_at > datetime.now(timezone.utc)),
            )
        stmt = stmt.order_by(UserMemory.updated_at.desc(), UserMemory.created_at.desc())
        return list(self.db.scalars(stmt).all())

    def list_by_user_and_status(self, user_id: str, status: str) -> list[UserMemory]:
        stmt = (
            select(UserMemory)
            .where(UserMemory.user_id == user_id, UserMemory.status == status)
            .order_by(UserMemory.updated_at.desc(), UserMemory.created_at.desc())
        )
        return list(self.db.scalars(stmt).all())

    def expire_due(self, user_id: str) -> int:
        now = datetime.now(timezone.utc)
        due = list(
            self.db.scalars(
                select(UserMemory).where(
                    UserMemory.user_id == user_id,
                    UserMemory.status == "active",
                    UserMemory.expires_at.is_not(None),
                    UserMemory.expires_at <= now,
                )
            ).all()
        )
        for memory in due:
            memory.status = "expired"
            memory.is_enabled = False
        if due:
            self.db.commit()
        return len(due)

    def find_by_content_hash(self, user_id: str, content_hash: str) -> UserMemory | None:
        return self.db.scalars(
            select(UserMemory)
            .where(UserMemory.user_id == user_id, UserMemory.content_hash == content_hash)
            .limit(1)
        ).first()

    def get_by_user(self, memory_id: str, user_id: str) -> UserMemory | None:
        stmt = (
            select(UserMemory)
            .where(UserMemory.id == memory_id, UserMemory.user_id == user_id)
            .limit(1)
        )
        return self.db.scalars(stmt).first()

    def save(self, memory: UserMemory) -> UserMemory:
        self.db.add(memory)
        self.db.commit()
        self.db.refresh(memory)
        return memory

    def flush(self, memory: UserMemory) -> UserMemory:
        self.db.add(memory)
        self.db.flush()
        return memory

    def delete(self, memory: UserMemory) -> None:
        self.db.delete(memory)
        self.db.commit()
