from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.user_memory import UserMemory


class UserMemoryRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_by_user(self, user_id: str, *, enabled_only: bool = False) -> list[UserMemory]:
        stmt = select(UserMemory).where(UserMemory.user_id == user_id)
        if enabled_only:
            stmt = stmt.where(UserMemory.is_enabled.is_(True))
        stmt = stmt.order_by(UserMemory.updated_at.desc(), UserMemory.created_at.desc())
        return list(self.db.scalars(stmt).all())

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

    def delete(self, memory: UserMemory) -> None:
        self.db.delete(memory)
        self.db.commit()
