from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.message import Message


class MessageRepository:
    """消息数据访问层。

    Message 的权限锚点是 conversation_id。Repository 不知道当前用户是谁，
    因此 route/service 必须在调用前先确认 conversation 属于 current_user。
    """

    def __init__(self, db: Session):
        self.db = db

    def list_by_conversation(self, conversation_id: str) -> list[Message]:
        # prompt 组装和前端展示都依赖稳定顺序。新消息优先用 sequence 解决同时间戳问题；
        # 旧消息 sequence 为空时仍按 created_at 兼容。
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc(), Message.sequence.asc())
        )
        return list(self.db.scalars(stmt).all())

    def create(self, message: Message) -> Message:
        # Repository 只 flush；一轮聊天可能还要同时写会话、附件和 assistant 占位消息。
        self._assign_sequence_if_needed(message)
        self.db.add(message)
        self.db.flush()
        self.db.refresh(message)
        return message

    def save(self, message: Message) -> Message:
        # 这里只登记变更；流式完成/失败等业务边界由 MessageService 提交。
        self._assign_sequence_if_needed(message)
        self.db.add(message)
        self.db.flush()
        self.db.refresh(message)
        return message

    def _assign_sequence_if_needed(self, message: Message) -> None:
        if getattr(message, "sequence", None) is not None:
            return
        conversation_id = getattr(message, "conversation_id", None)
        if not conversation_id:
            return
        current_max = self.db.scalar(
            select(func.coalesce(func.max(Message.sequence), 0)).where(
                Message.conversation_id == conversation_id
            )
        )
        message.sequence = int(current_max or 0) + 1

    def get_by_id_and_conversation(self, message_id: str, conversation_id: str) -> Message | None:
        # 永远不要只按 message_id 查公开资源；必须带 conversation_id 防止跨会话访问。
        stmt = (
            select(Message)
            .where(Message.id == message_id, Message.conversation_id == conversation_id)
            .limit(1)
        )
        return self.db.scalars(stmt).first()

    def list_by_ids_and_conversation(self, conversation_id: str, message_ids: list[str]) -> list[Message]:
        # 批量操作不能信任客户端提交的 message_ids。先用 conversation_id 收口，
        # 后续删除、RAG 日志断链等副作用都只能使用这里确认过归属的消息。
        if not message_ids:
            return []
        stmt = select(Message).where(
            Message.conversation_id == conversation_id,
            Message.id.in_(message_ids),
        )
        return list(self.db.scalars(stmt).all())

    def mark_stale_streaming_messages(self, conversation_id: str) -> int:
        # 前端切会话/服务重启可能留下 streaming 消息。读取消息列表时顺手把长时间未更新的流式消息标失败，
        # 避免用户永远看到“模型正在思考”。15 分钟是保守阈值，防止误伤真实慢请求。
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

        self.db.flush()
        return len(stale_messages)

    def delete(self, message: Message) -> None:
        self.db.delete(message)
        self.db.flush()

    def bulk_delete(self, conversation_id: str, message_ids: list[str]) -> int:
        # bulk_delete 仍然按 conversation_id 收口，传入其他会话的 message_id 不会被删除。
        if not message_ids:
            return 0

        messages = self.list_by_ids_and_conversation(conversation_id, message_ids)
        for message in messages:
            self.db.delete(message)
        self.db.flush()
        return len(messages)

    def delete_by_conversation(self, conversation_id: str) -> int:
        # 当前主要给内部清理使用；Conversation ORM cascade 已经能处理普通会话删除。
        stmt = select(Message).where(Message.conversation_id == conversation_id)
        messages = list(self.db.scalars(stmt).all())
        for message in messages:
            self.db.delete(message)
        self.db.flush()
        return len(messages)
