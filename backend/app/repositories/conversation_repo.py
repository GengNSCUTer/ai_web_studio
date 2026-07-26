from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.conversation import Conversation


class ConversationRepository:
    """会话数据访问层。

    Repository 只表达数据库查询形状：按用户列出、按用户取单条、保存、触碰更新时间、删除。
    权限判断的关键是所有公开读写都必须走 get_by_user/list_by_user，而不是直接 db.get(id)。
    """

    def __init__(self, db: Session):
        self.db = db

    def list_by_user(self, user_id: str, *, limit: int | None = None, offset: int = 0) -> list[Conversation]:
        # 列表页排序规则在这里固定：置顶优先，其次最近更新，再按创建时间兜底。
        stmt = (
            select(Conversation)
            .where(Conversation.user_id == user_id)
            .order_by(
                Conversation.is_pinned.desc(),
                Conversation.updated_at.desc(),
                Conversation.created_at.desc(),
                Conversation.id.desc(),
            )
        )
        if offset > 0:
            stmt = stmt.offset(offset)
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.db.scalars(stmt).all())

    def get_by_user(self, conversation_id: str, user_id: str) -> Conversation | None:
        # 这是会话域最重要的权限查询：必须同时匹配 id 和 user_id。
        # Message 没有 user_id，因此消息 API 需要先调用它确认会话归属。
        stmt = (
            select(Conversation)
            .where(Conversation.id == conversation_id, Conversation.user_id == user_id)
            .limit(1)
        )
        return self.db.scalars(stmt).first()

    def create(self, conversation: Conversation) -> Conversation:
        # Repository 只把变更 flush 到当前事务；commit/rollback 由 Service/用例编排层控制。
        # flush 后可以拿到数据库默认值和主键，但其他事务仍看不到这条未提交记录。
        self.db.add(conversation)
        self.db.flush()
        self.db.refresh(conversation)
        return conversation

    def save(self, conversation: Conversation) -> Conversation:
        # save 不擅自提交，避免一次跨 Repository 业务被切成多个不可回滚的小事务。
        self.db.add(conversation)
        self.db.flush()
        self.db.refresh(conversation)
        return conversation

    def update_context_summary(
        self,
        conversation_id: str,
        *,
        context_summary: str | None,
        context_summary_boundary_message_id: str | None,
    ) -> Conversation | None:
        # 该方法是上下文治理内部入口，不做 user_id 校验。
        # 公开 API 不应直接暴露它；如果未来暴露，需要改成 conversation_id + user_id 双条件。
        conversation = self.db.get(Conversation, conversation_id)
        if not conversation:
            return None

        conversation.context_summary = context_summary
        conversation.context_summary_boundary_message_id = context_summary_boundary_message_id
        self.db.add(conversation)
        self.db.flush()
        self.db.refresh(conversation)
        return conversation

    def touch(self, conversation_id: str) -> None:
        # touch 用于消息增删、生成完成后刷新会话排序；它只更新 updated_at，不读取对象。
        stmt = (
            update(Conversation)
            .where(Conversation.id == conversation_id)
            .values(updated_at=func.now())
        )
        self.db.execute(stmt)

    def delete(self, conversation: Conversation) -> None:
        # 删除 conversation 会通过 ORM cascade 删除 messages/shares。
        # 无外键级联的引用表需要在 service 中先解除引用。
        self.db.delete(conversation)
        self.db.flush()
