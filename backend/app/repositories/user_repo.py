from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.user import User


class UserRepository:
    """用户数据访问层：只封装查询/写入，不决定认证业务规则。"""

    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, user_id: str) -> User | None:
        # 主键查询走 Session.get，语义清晰，也能利用 SQLAlchemy identity map。
        return self.db.get(User, user_id)

    def get_by_email(self, email: str) -> User | None:
        # 登录邮箱大小写不敏感；Service 层也会做 strip/lower，Repository 再兜底。
        stmt = select(User).where(func.lower(User.email) == email.lower()).limit(1)
        return self.db.scalars(stmt).first()

    def get_by_username(self, username: str) -> User | None:
        stmt = select(User).where(func.lower(User.username) == username.lower()).limit(1)
        return self.db.scalars(stmt).first()

    def find_existing_identity(self, *, email: str, username: str) -> User | None:
        # 注册去重同时检查 email 和 username，避免业务层发起两次查询。
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
        # Repository 只登记待写入对象；commit/rollback 由业务层控制事务边界。
        self.db.add(user)
        self.db.flush()
        self.db.refresh(user)
        return user
