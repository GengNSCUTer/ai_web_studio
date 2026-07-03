from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class Conversation(Base):
    """会话表是聊天域的聚合根。

    第一性原则：所有消息、附件、工具轨迹、RAG 来源最终都要挂到某个会话上；
    因此用户权限校验必须先证明“当前用户拥有这个 conversation_id”，后续才允许读取
    该会话下的 message/attachment/tool trace 等子资源。
    """

    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    # user_id 允许为空主要是历史兼容；正常登录态创建的会话都应该写入 user_id。
    # 查询用户私有会话时必须使用 ConversationRepository.get_by_user/list_by_user。
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    # project_id 只表示“会话归属哪个工作区”，不替代 user_id 权限校验。
    # 创建/移动会话到工作区时，需要额外确认 project 属于同一个用户。
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(255), default="New Chat")
    model_name: Mapped[str] = mapped_column(String(128))
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    # context_summary 是上下文治理内部维护的滚动摘要，不应由普通 PATCH 会话接口直接改写。
    # boundary_message_id 表示“摘要覆盖到哪条消息为止”，prompt 组装会从该消息之后继续拼接近期历史。
    context_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    context_summary_boundary_message_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    context_summary_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # prompt prefix 观测字段只用于缓存/诊断，不参与业务权限判断。
    last_prompt_prefix_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_prompt_prefix_token_count: Mapped[int | None] = mapped_column(nullable=True)
    # 置顶和归档是列表展示状态，不代表软删除；删除会话仍然是物理删除。
    is_pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user = relationship("User", back_populates="conversations")
    project = relationship("Project", back_populates="conversations")
    # 删除会话会级联删除消息；Message 又会级联删除附件。
    # 这能保持主数据干净，但删除前必须先处理不能级联的外部引用，例如 RAG retrieval log。
    messages = relationship("Message", back_populates="conversation", cascade="all, delete-orphan")
    shares = relationship("ConversationShare", back_populates="conversation", cascade="all, delete-orphan")
