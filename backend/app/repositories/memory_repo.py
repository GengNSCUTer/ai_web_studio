from datetime import datetime, timezone
from contextlib import contextmanager

from pgvector.sqlalchemy import HALFVEC, Vector
from sqlalchemy import and_, cast, func, literal_column, or_, select, text, update
from sqlalchemy.orm import Session, defer

from app.models.user_memory import UserMemory
from app.models.user import User
from app.models.project import Project
from app.models.conversation import Conversation
from app.core.memory_search_schema import ANN_DIMENSIONS


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

    def _retrieval_filter(self, user_id: str, project_id: str | None):
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
        return (
            UserMemory.user_id == user_id, UserMemory.status == "active",
            UserMemory.is_enabled.is_(True), scope,
            or_(UserMemory.expires_at.is_(None), UserMemory.expires_at > datetime.now(timezone.utc)),
        )

    def retrieval_metadata(self, user_id: str, project_id: str | None) -> list[list]:
        """只取轻量版本清单；缓存不得依据 TTL 猜测记忆是否仍有效。"""
        if self.db.get_bind().dialect.name == "postgresql":
            # 时间流逝不会写修订号；最早到期边界变化仍须让旧选择失效。
            next_expiry = select(UserMemory.expires_at).where(
                *self._retrieval_filter(user_id, project_id), UserMemory.expires_at.is_not(None)
            ).order_by(UserMemory.expires_at).limit(1).scalar_subquery()
            row = self.db.execute(select(User.memory_revision, next_expiry).where(User.id == user_id)).first()
            revision, expiry = row if row else (None, None)
            return [revision, expiry.isoformat() if expiry else None]
        source_scope = select(Conversation.project_id).where(
            Conversation.id == UserMemory.source_conversation_id,
            Conversation.user_id == user_id).scalar_subquery()
        rows = self.db.execute(select(UserMemory.id, UserMemory.version, UserMemory.updated_at,
            UserMemory.expires_at, UserMemory.project_id, UserMemory.source_conversation_id, source_scope)
            .where(*self._retrieval_filter(user_id, project_id)).order_by(UserMemory.id)).all()
        return [[value.isoformat() if isinstance(value, datetime) else value for value in row] for row in rows]

    def _search_deadline(self):
        """线程取消不能终止已发出的 SQL，数据库也设置有限等待。"""
        self.db.execute(text("SELECT set_config('statement_timeout', '1500ms', true), "
                             "set_config('lock_timeout', '250ms', true)"))

    def retrieval_candidates(self, user_id, project_id, query, signature, *, route_limit=32):
        """按相关性而非最新 N 条召回；只返回候选 ID，不搬运全量正文。"""
        from app.services.memory_service import MemoryService

        self._search_deadline()
        filters = self._retrieval_filter(user_id, project_id)
        terms = sorted(MemoryService._search_terms(query))[:64]
        ids = list(self.db.scalars(select(UserMemory.id).where(*filters,
            UserMemory.memory_type.in_(["profile", "instruction"]), UserMemory.sensitivity != "sensitive"
        ).order_by(UserMemory.importance.desc(), UserMemory.updated_at.desc(), UserMemory.id).limit(8)))
        if terms:
            tsquery = func.to_tsquery("simple", " | ".join(terms))
            score = func.ts_rank_cd(UserMemory.search_vector, tsquery)
            ids.extend(self.db.scalars(select(UserMemory.id).where(*filters,
                UserMemory.search_vector.op("@@")(tsquery), UserMemory.sensitivity != "sensitive"
            ).order_by(score.desc(), UserMemory.id).limit(route_limit)))
        # 无向量的新事实有有限补建机会；老事实通过完整词法/向量索引参与检索。
        pending_ids = list(self.db.scalars(select(UserMemory.id).where(*filters,
            UserMemory.memory_type.not_in(["profile", "instruction"]), UserMemory.sensitivity != "sensitive",
            or_(UserMemory.search_embedding.is_(None), UserMemory.embedding_signature != signature),
            or_(UserMemory.embedding_retry_after.is_(None), UserMemory.embedding_retry_after <= datetime.now(timezone.utc))
        ).order_by(UserMemory.updated_at, UserMemory.id).limit(route_limit)))
        ids.extend(pending_ids)
        has_facts = self.db.scalar(select(select(UserMemory.id).where(*filters,
            UserMemory.memory_type.not_in(["profile", "instruction"]), UserMemory.sensitivity != "sensitive").exists()))
        return list(dict.fromkeys(ids)), bool(has_facts), set(pending_ids)

    def semantic_candidates(self, user_id, project_id, vector, signature, dimensions, *, limit=32):
        """小范围精确、大范围可用 HNSW；两者都在数据库执行，权限条件不放宽。"""
        self._search_deadline()
        filters = (*self._retrieval_filter(user_id, project_id),
            UserMemory.memory_type.not_in(["profile", "instruction"]),
            UserMemory.sensitivity != "sensitive", UserMemory.embedding_signature == signature,
            UserMemory.search_embedding.is_not(None),
            UserMemory.search_dimensions == literal_column(str(int(dimensions))))
        # 最多数到 257 就停止，不为判断索引路径读取全量 ID 或正文。
        size = self.db.scalar(select(func.count()).select_from(select(UserMemory.id).where(*filters).limit(257).subquery()))
        use_ann = size > 256 and dimensions in ANN_DIMENSIONS
        expression = cast(UserMemory.search_embedding, Vector(dimensions) if dimensions <= 2000 else HALFVEC(dimensions)) if use_ann else UserMemory.search_embedding
        distance = expression.cosine_distance(vector)
        if use_ann:
            self.db.execute(text("SELECT set_config('hnsw.iterative_scan', 'strict_order', true), "
                "set_config('hnsw.ef_search', '100', true), set_config('enable_sort', 'off', true)"))
        rows = self.db.execute(select(UserMemory.id, distance).where(*filters).order_by(distance).limit(limit)).all()
        topup = use_ann and (len(rows) < min(limit, size) or not any(1. - row[1] >= .45 for row in rows))
        if topup:
            # 多租户过滤后不足时走显式精确距离；不关闭权限或混用模型。
            self.db.execute(text("SET LOCAL enable_sort = on"))
            distance = UserMemory.search_embedding.cosine_distance(vector)
            rows = self.db.execute(select(UserMemory.id, distance).where(*filters)
                .order_by(distance + 0, UserMemory.id).limit(limit)).all()
        elif use_ann:
            self.db.execute(text("SET LOCAL enable_sort = on"))
        return {row[0]: max(-1., min(1., 1. - row[1])) for row in rows}, {
            "database_search": "hnsw" if use_ann else "exact", "exact_topup": topup,
            "semantic_candidates": len(rows)}

    def list_for_retrieval(self, user_id: str, project_id: str | None, *, memory_ids=None,
                           include_vectors=True) -> list[UserMemory]:
        stmt = select(UserMemory).where(*self._retrieval_filter(user_id, project_id))
        if memory_ids is not None:
            if not memory_ids:
                return []
            stmt = stmt.where(UserMemory.id.in_(memory_ids))
        if not include_vectors:
            stmt = stmt.options(defer(UserMemory.embedding_vector), defer(UserMemory.embedding_signature),
                                defer(UserMemory.embedding_text_hash))
        stmt = stmt.order_by(UserMemory.updated_at.desc(), UserMemory.id).execution_options(populate_existing=True)
        return list(self.db.scalars(stmt))

    def expire_due(self, user_id: str) -> int:
        # 到期也是记忆状态变更，必须与激活/替换使用同一用户锁。
        if not self._mutating:
            with self.mutation(user_id):
                return self.expire_due(user_id)
        now = datetime.now(timezone.utc)
        result = self.db.execute(
            update(UserMemory).where(UserMemory.user_id == user_id, UserMemory.status == "active",
                                     UserMemory.expires_at.is_not(None), UserMemory.expires_at <= now)
            .values(status="expired", is_enabled=False, version=UserMemory.version + 1)
            .execution_options(synchronize_session=False)
        )
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
