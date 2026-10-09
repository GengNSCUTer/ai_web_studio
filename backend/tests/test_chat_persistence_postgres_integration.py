from __future__ import annotations

import asyncio
import os
import threading
from datetime import timedelta
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models import *  # noqa: F403 - 建表需要导入所有模型。
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.observability import ChatRuntimeMetric
from app.models.user import User
from app.models.user_memory import MemoryExtractionJob
from app.models.user_setting import UserSetting
from app.api.routes.chat import _build_streaming_response
from app.repositories.message_repo import MessageRepository
from app.services.message_service import MessageService
from app.services.chat_persistence_service import ChatPersistenceSnapshot, persist_stream_result


TEST_POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")


@unittest.skipUnless(TEST_POSTGRES_URL, "set TEST_POSTGRES_URL to run PostgreSQL integration tests")
class ChatPersistencePostgresIntegrationTest(unittest.TestCase):
    """验证真实 psycopg 异步驱动、CAS 边界和事件循环响应能力。"""

    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_POSTGRES_URL is not None
        source_url = make_url(TEST_POSTGRES_URL)
        cls.database_name = f"aiws_chat_async_test_{uuid4().hex}"
        cls.admin_engine = create_engine(source_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
        cls.database_url = source_url.set(database=cls.database_name)
        with cls.admin_engine.connect() as connection:
            connection.execute(text(f'create database "{cls.database_name}"'))
        cls.engine = create_engine(cls.database_url, pool_pre_ping=True)
        with cls.engine.begin() as connection:
            connection.execute(text("create extension if not exists vector"))
        Base.metadata.create_all(bind=cls.engine)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()
        with cls.admin_engine.connect() as connection:
            connection.execute(text(f'drop database if exists "{cls.database_name}" with (force)'))
        cls.admin_engine.dispose()

    def setUp(self) -> None:
        with Session(self.engine) as db:
            user = User(email=f"chat-async-{uuid4()}@example.com", username=f"chat-async-{uuid4()}")
            db.add(user)
            db.flush()
            conversation = Conversation(user_id=user.id, title="异步持久化测试", model_name="test-model")
            db.add(conversation)
            db.flush()
            message = Message(conversation_id=conversation.id, role="assistant", content="", status="streaming")
            db.add(message)
            db.commit()
            self.user_id = user.id
            self.conversation_id = conversation.id
            self.message_id = message.id
            self.generation_id = message.generation_id

        self.async_engine = create_async_engine(self.database_url, pool_pre_ping=True)
        self.async_session_factory = async_sessionmaker(
            bind=self.async_engine, expire_on_commit=False, autoflush=False
        )

    def tearDown(self) -> None:
        asyncio.run(self.async_engine.dispose())

    def _snapshot(self, *, generation_id: str | None = None, status: str = "done") -> ChatPersistenceSnapshot:
        return ChatPersistenceSnapshot(
            assistant_message_id=self.message_id,
            conversation_id=self.conversation_id,
            user_id=self.user_id,
            generation_id=generation_id or self.generation_id,
            provider_type="openai_compatible",
            model_name="test-model",
            status=status,
            content="测试回答",
            reasoning_content=None,
            external_sources=None,
            context_stats={"test_case": "chat_async"},
        )

    def _persist(self, snapshot: ChatPersistenceSnapshot) -> bool:
        with patch(
            "app.services.chat_persistence_service._get_async_session_factory",
            return_value=self.async_session_factory,
        ):
            return asyncio.run(persist_stream_result(snapshot))

    def test_async_result_persists_message_metric_and_fences_old_generation(self) -> None:
        self.assertTrue(self._persist(self._snapshot()))
        with Session(self.engine) as db:
            message = db.get(Message, self.message_id)
            assert message is not None
            self.assertEqual(message.status, "done")
            self.assertEqual(message.content, "测试回答")
            metric = db.scalar(
                select(ChatRuntimeMetric).where(ChatRuntimeMetric.assistant_message_id == self.message_id)
            )
            self.assertIsNotNone(metric)
            assert metric is not None
            self.assertEqual(metric.status, "done")

            message.generation_id = str(uuid4())
            message.status = "streaming"
            message.content = ""
            db.commit()

        self.assertFalse(self._persist(self._snapshot()))
        with Session(self.engine) as db:
            message = db.get(Message, self.message_id)
            assert message is not None
            self.assertEqual(message.status, "streaming")
            self.assertEqual(message.content, "")

    def test_done_enqueues_memory_extraction_when_user_opted_in(self) -> None:
        """验证既有同步记忆任务逻辑能在 AsyncSession.run_sync 中执行。"""

        with Session(self.engine) as db:
            db.add(
                UserSetting(
                    user_id=self.user_id,
                    memory_auto_candidate_enabled=True,
                    memory_auto_candidate_turn_interval=1,
                )
            )
            assistant = db.get(Message, self.message_id)
            assistant.sequence = 2
            source = Message(conversation_id=self.conversation_id, role="user", sequence=1,
                content="我偏好简洁回答", status="done", created_at=assistant.created_at - timedelta(microseconds=1))
            db.add(source)
            db.commit()
            source_id = source.id

        self.assertTrue(self._persist(self._snapshot()))
        with Session(self.engine) as db:
            job = db.scalar(
                select(MemoryExtractionJob).where(
                    MemoryExtractionJob.idempotency_key
                    == f"memory-extract:{self.conversation_id}:{source_id}"
                )
            )
            self.assertIsNotNone(job)
            self.assertEqual(job.source_message_ids, source_id)

    def test_locked_postgres_row_does_not_block_asyncio_heartbeat(self) -> None:
        ready = threading.Event()
        release = threading.Event()

        def lock_message() -> None:
            with Session(self.engine) as db:
                db.execute(select(Message.id).where(Message.id == self.message_id).with_for_update())
                ready.set()
                release.wait(timeout=5)
                db.commit()

        locker = threading.Thread(target=lock_message, daemon=True)
        locker.start()
        self.assertTrue(ready.wait(timeout=3))
        try:
            async def verify() -> int:
                with patch(
                    "app.services.chat_persistence_service._get_async_session_factory",
                    return_value=self.async_session_factory,
                ):
                    task = asyncio.create_task(persist_stream_result(self._snapshot()))
                    heartbeat_ticks = 0
                    for _ in range(6):
                        await asyncio.sleep(0.025)
                        if not task.done():
                            heartbeat_ticks += 1
                    release.set()
                    self.assertTrue(await asyncio.wait_for(task, timeout=3))
                    return heartbeat_ticks

            self.assertGreaterEqual(asyncio.run(verify()), 3)
        finally:
            release.set()
            locker.join(timeout=3)

    def test_event_stream_route_uses_async_persistence_and_emits_done(self) -> None:
        """测试真实流式出口，而不只测试持久化函数。"""

        class Provider:
            async def stream_chat_events(self, **_: object):
                yield SimpleNamespace(type="answer_delta", text="真实 PostgreSQL 流式回答")

        with Session(self.engine) as db:
            conversation = db.get(Conversation, self.conversation_id)
            message = db.get(Message, self.message_id)
            assert conversation is not None and message is not None
            context = SimpleNamespace(
                message_service=MessageService(MessageRepository(db)),
                conversation=conversation,
                assistant_message=message,
                generation_id=self.generation_id,
                provider_type="openai_compatible",
                base_url="https://example.invalid/v1",
                api_key=None,
                resolved_model="test-model",
                history_messages=[{"role": "user", "content": "测试"}],
                temperature=0.2,
                top_p=0.9,
                max_tokens=32,
                thinking_enabled=False,
                thinking_budget=None,
                prompt_cache_key=None,
                prompt_cache_breakpoint=0,
                context_stats={},
                context_notices=[],
                context_details={},
                tool_events=[],
                external_sources=[],
            )

            async def verify() -> str:
                with patch(
                    "app.services.chat_persistence_service._get_async_session_factory",
                    return_value=self.async_session_factory,
                ):
                    response = _build_streaming_response(context, Provider(), event_stream=True)
                    # 长时间模型流开始前，请求侧同步会话已不持有数据库事务。
                    self.assertFalse(db.in_transaction())
                    return "".join([chunk async for chunk in response.body_iterator])

            body = asyncio.run(verify())

        self.assertIn('"type": "done"', body)
        with Session(self.engine) as db:
            message = db.get(Message, self.message_id)
            assert message is not None
            self.assertEqual(message.status, "done")
            self.assertEqual(message.content, "真实 PostgreSQL 流式回答")

    def test_cancelled_event_stream_persists_partial_answer(self) -> None:
        """取消时仍保存部分输出，避免 assistant 长期保持 streaming。"""

        class Provider:
            async def stream_chat_events(self, **_: object):
                yield SimpleNamespace(type="answer_delta", text="已经生成的前半句")
                raise asyncio.CancelledError

        with Session(self.engine) as db:
            conversation = db.get(Conversation, self.conversation_id)
            message = db.get(Message, self.message_id)
            assert conversation is not None and message is not None
            context = SimpleNamespace(
                message_service=MessageService(MessageRepository(db)),
                conversation=conversation,
                assistant_message=message,
                generation_id=self.generation_id,
                provider_type="openai_compatible",
                base_url="https://example.invalid/v1",
                api_key=None,
                resolved_model="test-model",
                history_messages=[{"role": "user", "content": "测试"}],
                temperature=0.2,
                top_p=0.9,
                max_tokens=32,
                thinking_enabled=False,
                thinking_budget=None,
                prompt_cache_key=None,
                prompt_cache_breakpoint=0,
                context_stats={},
                context_notices=[],
                context_details={},
                tool_events=[],
                external_sources=[],
            )

            async def verify() -> None:
                with patch(
                    "app.services.chat_persistence_service._get_async_session_factory",
                    return_value=self.async_session_factory,
                ):
                    response = _build_streaming_response(context, Provider(), event_stream=True)
                    with self.assertRaises(asyncio.CancelledError):
                        async for _ in response.body_iterator:
                            pass

            asyncio.run(verify())

        with Session(self.engine) as db:
            message = db.get(Message, self.message_id)
            assert message is not None
            self.assertEqual(message.status, "cancelled")
            self.assertEqual(message.content, "已经生成的前半句")


if __name__ == "__main__":
    unittest.main()
