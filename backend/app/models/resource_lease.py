"""用户级容量租约：短事务登记，进程退出后可自动过期。"""

from datetime import datetime
from sqlalchemy import BigInteger, DateTime, Index, String
from sqlalchemy.orm import Mapped, mapped_column
from app.core.database import Base


class ResourceLease(Base):
    __tablename__ = "resource_leases"
    __table_args__ = (Index("ix_resource_leases_user_resource_expiry", "user_id", "resource", "expires_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36))
    resource: Mapped[str] = mapped_column(String(24))
    reserved_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
