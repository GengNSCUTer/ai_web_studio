from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Message(Base):
    """消息表保存一轮对话历史中的原始事实。

    Message 没有 user_id，这是一个有意的归属设计：消息属于会话，会话属于用户。
    所有公开 API 如果要读写消息，必须先通过 conversation_id 校验会话归属；
    不能只拿 message_id 直接查，否则会绕过用户边界。
    """

    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    # conversation_id 是消息唯一的权限锚点；MessageRepository 的查询必须带上它。
    conversation_id: Mapped[str] = mapped_column(ForeignKey("conversations.id"), index=True)
    # sequence 是会话内显式消息顺序。created_at 受数据库时间精度影响，同一时间戳下不足以判断最后一轮。
    # 旧数据允许为空；新消息由 MessageRepository.create/save 在写入前分配递增序号。
    sequence: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    # 每次重生成/编辑重答都会换一个 opaque generation token。流式收口必须带上
    # 创建它时看到的 token，避免旧请求在新请求之后覆盖 assistant 内容。
    generation_id: Mapped[str] = mapped_column(String(36), default=lambda: str(uuid4()), nullable=False)
    # role 会被 prompt builder 原样传给模型。公开入口应只允许用户创建 user 消息；
    # assistant/system 消息只能由聊天编排链路内部创建，避免客户端伪造历史角色。
    role: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    # reasoning_content 与 external_sources 是模型输出的附加可视化数据。
    # external_sources 当前以 JSON 字符串保存，序列化/反序列化由上层负责。
    reasoning_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_sources: Mapped[str | None] = mapped_column(Text, nullable=True)
    # status 至少包含 done/streaming/failed/cancelled 等状态；前端依赖它区分流式中、失败和停止。
    status: Mapped[str] = mapped_column(String(32), default="done")
    # 应用侧先生成高精度 UTC 时间；数据库默认值保留给 SQL 直写/旧迁移路径兜底。
    # SQLite 的 CURRENT_TIMESTAMP 只有秒级精度，同一批消息可能因此无法判断先后。
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    conversation = relationship("Conversation", back_populates="messages")
    # 附件和消息强绑定，删除消息时删除附件元数据；物理文件清理需要由上传/文件服务单独处理。
    attachments = relationship("Attachment", back_populates="message", cascade="all, delete-orphan")
