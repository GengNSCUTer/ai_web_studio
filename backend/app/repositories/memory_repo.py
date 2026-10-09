from datetime import datetime, timezone
from contextlib import contextmanager

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from app.models.user_memory import UserMemory
from app.models.user import User
from app.models.project import Project
from app.models.conversation import Conversation


class UserMemoryRepository:
    def __init__(self, db: Session):
        self.db = db
        self._mutating = False

    @contextmanager
    def mutation(self, user_id: str):
        """按用户串行化短事务；刷新 ORM 快照，异常时整体回滚。"""
        with self.db.no_autoflush:
            user = self.db.scalar(select(User).where(User.id == user_id).with_for_update())
            if user is None:
                raise ValueError("用户不存在")
            self._mutating = True
            try:
                yield
                self.db.commit()
            except Exception:
                self.db.rollback()
                raise
            finally:
                self._mutating = False

    def fresh(self, memory: UserMemory, expected_version: int | None = None, *, idempotent_status: str | None = None) -> UserMemory:
        """旧页面、旧 Session 不得覆盖最新记忆状态。"""
        version = expected_version if expected_version is not None else memory.version
        current = self.db.scalar(
            select(UserMemory).where(UserMemory.id == memory.id, UserMemory.user_id == memory.user_id)
            .execution_options(populate_existing=True)
        )
        if current is None:
            raise ValueError("记忆不存在")
        if current.version != version and current.status != idempotent_status:
            raise ValueError("记忆版本已变化，请刷新后重试")
        return current

    def owns_project(self, user_id: str, project_id: str) -> bool:
        return self.db.scalar(select(Project.id).where(Project.id == project_id, Project.user_id == user_id)) is not None

    def list_by_user(self, user_id: str, *, enabled_only: bool = False) -> list[UserMemory]:
        stmt = select(UserMemory).where(UserMemory.user_id == user_id)
        if enabled_only:
            stmt = stmt.where(
                UserMemory.is_enabled.is_(True),
                UserMemory.status == "active",
                or_(UserMemory.expires_at.is_(None), UserMemory.expires_at > datetime.now(timezone.utc)),
            )
        stmt = stmt.order_by(UserMemory.updated_at.desc(), UserMemory.created_at.desc()).execution_options(populate_existing=True)
        return list(self.db.scalars(stmt).all())

    def list_by_user_and_status(self, user_id: str, status: str) -> list[UserMemory]:
        stmt = (
            select(UserMemory)
            .where(UserMemory.user_id == user_id, UserMemory.status == status)
            .order_by(UserMemory.updated_at.desc(), UserMemory.created_at.desc())
        )
        return list(self.db.scalars(stmt).all())

    def list_for_retrieval(self, user_id: str, project_id: str | None) -> list[UserMemory]:
        """检索前在 SQL 中隔离范围；旧记录的来源会话也必须属于当前用户。"""
        source = select(Conversation.id).where(
            Conversation.id == UserMemory.source_conversation_id,
            Conversation.user_id == user_id,
            or_(Conversation.project_id.is_(None), Conversation.project_id == project_id),
        ).exists()
        owned_project = select(Project.id).where(Project.id == UserMemory.project_id, Project.user_id == user_id).exists()
        scope = or_(
            and_(UserMemory.project_id == project_id, owned_project) if project_id else False,
            and_(UserMemory.project_id.is_(None),
                 or_(UserMemory.source_conversation_id.is_(None), source)),
        )
        stmt = select(UserMemory).where(
            UserMemory.user_id == user_id, UserMemory.status == "active",
            UserMemory.is_enabled.is_(True), scope,
            or_(UserMemory.expires_at.is_(None), UserMemory.expires_at > datetime.now(timezone.utc)),
        ).order_by(UserMemory.updated_at.desc(), UserMemory.id).execution_options(populate_existing=True)
        return list(self.db.scalars(stmt))

    def expire_due(self, user_id: str) -> int:
        now = datetime.now(timezone.utc)
        result = self.db.execute(
            update(UserMemory).where(UserMemory.user_id == user_id, UserMemory.status == "active",
                                     UserMemory.expires_at.is_not(None), UserMemory.expires_at <= now)
            .values(status="expired", is_enabled=False, version=UserMemory.version + 1)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount:
            self.db.commit()
        return result.rowcount

    def list_others(self, user_id: str, memory_id: str | None) -> list[UserMemory]:
        stmt = select(UserMemory).where(UserMemory.user_id == user_id, UserMemory.status == "active")
        if memory_id:
            stmt = stmt.where(UserMemory.id != memory_id)
        return list(self.db.scalars(stmt.execution_options(populate_existing=True)).all())

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
            .limit(1).execution_options(populate_existing=True)
        )
        return self.db.scalars(stmt).first()

    def save(self, memory: UserMemory) -> UserMemory:
        self.db.add(memory)
        if self._mutating:
            self.db.flush()
        else:
            self.db.commit()
        self.db.refresh(memory)
        return memory

    def flush(self, memory: UserMemory) -> UserMemory:
        self.db.add(memory)
        self.db.flush()
        return memory

    def delete(self, memory: UserMemory) -> None:
        # 有后继版本的记忆保留审计链，撤销代替破坏性删除。
        with self.mutation(memory.user_id):
            memory = self.fresh(memory)
            linked = self.db.scalar(select(UserMemory.id).where(UserMemory.supersedes_memory_id == memory.id).limit(1))
            if linked:
                raise ValueError("该记忆存在后继版本，请保留历史并使用撤销")
            self.db.delete(memory)
