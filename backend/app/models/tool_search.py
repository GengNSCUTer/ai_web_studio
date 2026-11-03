"""工具目录的派生检索数据；索引条目不能授予执行权限。"""

from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, Computed, DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


ANN_DIMENSIONS = (256, 384, 512, 768, 1024, 1536, 2048, 3072)


class SearchVectorComputed(Computed):
    """SQLite仅用于既有业务单测建表，不提供PostgreSQL检索能力。"""


@compiles(SearchVectorComputed, "sqlite")
def _sqlite_computed(element, compiler, **kwargs):
    return ""


class ToolSearchEntry(Base):
    __tablename__ = "tool_search_entries"
    __table_args__ = (
        Index("ix_tool_search_user_scope", "user_id", "scope"),
        Index("ix_tool_search_terms", "search_vector", postgresql_using="gin").ddl_if(dialect="postgresql"),
        CheckConstraint("embedding IS NULL OR vector_dims(embedding) = dimensions", name="ck_tool_search_dimensions").ddl_if(dialect="postgresql"),
        *(Index(f"ix_tool_search_hnsw_{dim}",
                text(f"(embedding::{ 'vector' if dim <= 2000 else 'halfvec' }({dim})) "
                     f"{'vector' if dim <= 2000 else 'halfvec'}_cosine_ops"),
                postgresql_using="hnsw", postgresql_where=text(f"dimensions = {dim}")).ddl_if(dialect="postgresql")
          for dim in ANN_DIMENSIONS),
    )

    scope: Mapped[str] = mapped_column(String(64), primary_key=True)
    tool_key: Mapped[str] = mapped_column(String(220), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_text: Mapped[str] = mapped_column(Text, nullable=False)
    lexical_text: Mapped[str] = mapped_column(Text, nullable=False)
    search_vector: Mapped[object | None] = mapped_column(TSVECTOR().with_variant(Text(), "sqlite"),
        SearchVectorComputed("to_tsvector('simple'::regconfig, lexical_text)", persisted=True), nullable=True)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
