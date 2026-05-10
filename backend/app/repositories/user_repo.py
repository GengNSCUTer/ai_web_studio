from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.user import User


class UserRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, user_id: str) -> User | None:
        return self.db.get(User, user_id)

    def get_by_email(self, email: str) -> User | None:
        stmt = select(User).where(func.lower(User.email) == email.lower()).limit(1)
        return self.db.scalars(stmt).first()

    def get_by_username(self, username: str) -> User | None:
        stmt = select(User).where(func.lower(User.username) == username.lower()).limit(1)
        return self.db.scalars(stmt).first()

    def find_existing_identity(self, *, email: str, username: str) -> User | None:
        stmt = (
            select(User)
            .where(
                or_(
                    func.lower(User.email) == email.lower(),
                    func.lower(User.username) == username.lower(),
                )
            )
            .limit(1)
        )
        return self.db.scalars(stmt).first()

    def create(self, user: User) -> User:
        self.db.add(user)
        self.db.commit()
        self.db.refresh(user)
        return user
