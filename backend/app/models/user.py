from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class User(Base):
    # ORM Model 只描述 users 表结构和关系，不处理密码校验/JWT 等业务逻辑。
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    # 当前字段保持 nullable 以兼容历史数据；唯一索引兜住并发注册重复。
    username: Mapped[str | None] = mapped_column(String(64), nullable=True)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # relationship 定义 ORM 对象导航关系；cascade 表示删除用户时同步删除其项目/记忆等子对象。
    conversations = relationship("Conversation", back_populates="user")
    projects = relationship("Project", back_populates="user", cascade="all, delete-orphan")
    settings = relationship("UserSetting", back_populates="user")
    memories = relationship("UserMemory", back_populates="user", cascade="all, delete-orphan")


Index("ux_users_email_lower", func.lower(User.email), unique=True)
Index("ux_users_username_lower", func.lower(User.username), unique=True)
