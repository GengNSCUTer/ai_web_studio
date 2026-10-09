"""Chat 流式结果的异步持久化边界。

Chat provider 的流式迭代运行在 asyncio 事件循环中，不能在生成结束时直接使用
请求依赖注入的同步 Session 做数据库 IO。本模块为 PostgreSQL 创建独立的
AsyncSession。消息与会话时间在同一个短事务中提交；指标与自动记忆任务仍然
是独立的尽力而为副作用，不允许它们改变聊天消息的结果。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.observability import ChatRuntimeMetric
from app.services.memory_candidate_runtime import MemoryExtractionJobService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChatPersistenceSnapshot:
    """流式收口需要的原子值，避免把请求 Session 中的 ORM 对象带入新会话。"""

    assistant_message_id: str
    conversation_id: str
    user_id: str
    generation_id: str | None
    provider_type: str
    model_name: str
    status: str
    content: str
    reasoning_content: str | None
    external_sources: str | None
    context_stats: dict[str, Any]


_async_engine = None
_async_session_factory: async_sessionmaker[AsyncSession] | None = None


def _get_async_session_factory() -> async_sessionmaker[AsyncSession]:
    """按需创建全局异步连接池，避免测试/CLI 导入时立即连接数据库。"""

    global _async_engine, _async_session_factory
    if _async_session_factory is None:
        # psycopg 3 的同一连接串可由 create_engine/create_async_engine 分别选择同步
        # 或异步实现；不再引入第二套数据库驱动。
        _async_engine = create_async_engine(
            settings.sqlalchemy_database_uri,
            pool_pre_ping=True,
        )
        _async_session_factory = async_sessionmaker(
            bind=_async_engine,
            expire_on_commit=False,
            autoflush=False,
        )
    return _async_session_factory


async def _persist_async(session: AsyncSession, snapshot: ChatPersistenceSnapshot) -> bool:
    """异步收口消息，并按原有边界处理尽力而为副作用。"""
    message_update = (
        update(Message)
        .where(
            Message.id == snapshot.assistant_message_id,
            Message.generation_id == snapshot.generation_id,
        )
        .values(
            content=snapshot.content,
            reasoning_content=snapshot.reasoning_content,
            external_sources=snapshot.external_sources,
            status=snapshot.status,
            updated_at=datetime.now(timezone.utc),
        )
    )
    result = await session.execute(message_update)
    if result.rowcount != 1:
        # generation_id 不匹配说明旧请求已经被新生成接管，不能再写任何副作用。
        await session.rollback()
        return False

    await session.execute(
        update(Conversation)
        .where(Conversation.id == snapshot.conversation_id)
        .values(updated_at=func.now())
    )
    await session.commit()

    try:
        metric = await session.scalar(
            select(ChatRuntimeMetric)
            .where(ChatRuntimeMetric.assistant_message_id == snapshot.assistant_message_id)
            .limit(1)
        )
        if not metric:
            metric = ChatRuntimeMetric(
                user_id=snapshot.user_id,
                conversation_id=snapshot.conversation_id,
                assistant_message_id=snapshot.assistant_message_id,
                provider_type=snapshot.provider_type,
                model_name=snapshot.model_name,
                status=snapshot.status,
            )
            session.add(metric)
        metric.provider_type = snapshot.provider_type
        metric.model_name = snapshot.model_name
        metric.status = snapshot.status
        metric.stats_json = json.dumps(snapshot.context_stats, ensure_ascii=False, default=str)
        await session.commit()
    except Exception:
        await session.rollback()
        logger.warning("Failed to persist chat runtime metrics")

    if snapshot.status == "done":
        # 记忆抽取任务本身有独立幂等键和事务；它不能阻塞或影响 assistant 结果收口。
        try:
            await session.run_sync(
                lambda sync_session: MemoryExtractionJobService(sync_session).enqueue_after_turn(
                    user_id=snapshot.user_id,
                    conversation_id=snapshot.conversation_id,
                    assistant_message_id=snapshot.assistant_message_id,
                )
            )
        except Exception:
            await session.rollback()
            logger.warning("Failed to enqueue memory candidate extraction job")
    return True


async def persist_stream_result(snapshot: ChatPersistenceSnapshot) -> bool:
    """非阻塞收口一次流式结果。

    生产 PostgreSQL 路径使用独立 AsyncSession，不复用请求的同步 Session。
    数据库出错时直接向调用方抛出；不要在结果可能已提交后盲目重试。
    """

    factory = _get_async_session_factory()
    async with factory() as session:
        return await _persist_async(session, snapshot)


async def dispose_async_engine() -> None:
    """测试和进程优雅停机时释放异步连接池。"""

    global _async_engine, _async_session_factory
    if _async_engine is not None:
        await _async_engine.dispose()
    _async_engine = None
    _async_session_factory = None
