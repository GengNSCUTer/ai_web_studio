"""验证摘要与历史在同一事务失效，以及网络等待后的并发校验。"""

import asyncio
import os
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models import *  # noqa: F403 - 创建完整测试数据库。
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.message_repo import MessageRepository
from app.services.chat_context_assembly_service import ChatContextAssemblyService
from app.services.context_governance_service import ContextBudgetPlanner, ContextGovernanceService
from app.services.message_service import MessageService


class SummaryLifecycleCases:
    def setUp(self):
        self.db = Session(self.engine, autoflush=False)
        user = User(email=f"summary-{uuid4()}@example.com", username=f"summary-{uuid4()}")
        self.db.add(user)
        self.db.flush()
        self.conversation = Conversation(user_id=user.id, title="摘要边界测试", model_name="test-summary")
        self.db.add(self.conversation)
        self.db.flush()
        self.repo = MessageRepository(self.db)
        self.service = MessageService(self.repo)
        self.rows = []
        start = datetime.now(timezone.utc) - timedelta(minutes=1)
        for index in range(12):
            row = Message(
                conversation_id=self.conversation.id,
                role="user" if index % 2 == 0 else "assistant",
                content=f"历史消息 {index}",
                status="done",
                created_at=start + timedelta(seconds=index),
            )
            self.repo.create(row)
            self.rows.append(row)
        self.assistant = self.repo.create(Message(
            conversation_id=self.conversation.id, role="assistant", content="", status="streaming"
        ))
        self.conversation.context_summary = "已压缩的旧历史"
        self.conversation.context_summary_boundary_message_id = self.rows[1].id
        self.conversation.context_summary_updated_at = start
        self.db.commit()
        self.assembly = ChatContextAssemblyService(
            db=self.db, user_id=user.id, conversation_repo=ConversationRepository(self.db),
            message_service=self.service, tool_trace_repo=None,
        )
        budget = replace(ContextBudgetPlanner.build(model_context_window=16000, context_mode="balanced"),
                         max_history_messages=2)
        self.runtime = SimpleNamespace(
            governance_service=ContextGovernanceService(budget=budget),
            resolved_model="test-summary", settings=SimpleNamespace(default_model="test-summary"),
            provider_type="openai_compatible", base_url="https://example.com/v1", provider_api_key=None,
        )

    def tearDown(self):
        self.db.close()

    def assert_summary_cleared(self):
        self.db.expire_all()
        self.assertIsNone(self.conversation.context_summary)
        self.assertIsNone(self.conversation.context_summary_boundary_message_id)
        self.assertIsNone(self.conversation.context_summary_updated_at)

    def test_delete_covered_message_clears_summary(self):
        self.assertTrue(self.service.delete_message(self.rows[0].id, self.conversation.id))
        self.assert_summary_cleared()

    def test_delete_boundary_clears_summary_immediately(self):
        self.assertTrue(self.service.delete_message(self.rows[1].id, self.conversation.id))
        self.assert_summary_cleared()

    def test_delete_recent_message_preserves_summary(self):
        self.service.delete_message(self.rows[-1].id, self.conversation.id)
        self.assertEqual(self.conversation.context_summary, "已压缩的旧历史")

    def test_bulk_delete_scopes_ids_before_invalidation(self):
        other = Conversation(user_id=self.conversation.user_id, title="另一个会话", model_name="test-summary")
        self.db.add(other)
        self.db.flush()
        foreign = self.repo.create(Message(conversation_id=other.id, role="user", content="其他会话"))
        self.db.commit()
        self.assertEqual(self.service.bulk_delete_messages(self.conversation.id, [foreign.id, "missing"]), 0)
        self.assertEqual(self.conversation.context_summary, "已压缩的旧历史")
        self.assertEqual(self.service.bulk_delete_messages(self.conversation.id, [self.rows[0].id, foreign.id]), 1)
        self.assert_summary_cleared()
        self.assertIsNotNone(self.db.get(Message, foreign.id))

    def test_delete_failure_rolls_back_summary_and_message(self):
        mid = self.rows[0].id
        with patch.object(self.repo, "delete", side_effect=RuntimeError("删除失败")):
            with self.assertRaises(RuntimeError):
                self.service.delete_message(mid, self.conversation.id)
        self.assertEqual(self.conversation.context_summary, "已压缩的旧历史")
        self.assertIsNotNone(self.db.get(Message, mid))

    def test_edit_covered_message_clears_summary(self):
        self.assistant.status = "done"
        self.db.commit()
        self.service.edit_and_reset_for_regeneration(
            user_message=self.rows[0], assistant_message=self.rows[1], content="修改后的历史",
            uploads=None, user_id=self.conversation.user_id,
        )
        self.assert_summary_cleared()

    def test_regenerate_covered_message_clears_summary(self):
        self.assistant.status = "done"
        self.db.commit()
        self.service.reset_assistant_for_regeneration(self.rows[1])
        self.assert_summary_cleared()

    def _refresh(self, *, snapshot=None):
        expected = snapshot if snapshot is not None else self.repo.history_snapshot(
            self.conversation.id, exclude_message_id=self.assistant.id
        )
        return asyncio.run(self.assembly._refresh_context_summary(
            conversation=self.conversation, runtime=self.runtime,
            expected_history=expected, assistant_id=self.assistant.id, generation_id=self.assistant.generation_id,
        ))

    def test_unchanged_history_refreshes_without_transaction_during_model_wait(self):
        async def summarize(**kwargs):
            self.assertFalse(self.db.in_transaction())
            return "新摘要"
        with patch("app.services.chat_context_assembly_service.ChatProviderService.complete_chat", side_effect=summarize):
            result = self._refresh()
        self.db.commit()
        self.assertEqual(result.summary, "新摘要")
        self.assertEqual(self.conversation.context_summary_boundary_message_id, self.rows[-3].id)

    def test_provider_failure_preserves_fallback(self):
        with patch("app.services.chat_context_assembly_service.ChatProviderService.complete_chat",
                   new=AsyncMock(side_effect=TimeoutError())):
            result = self._refresh()
        self.db.commit()
        self.assertEqual(result.stats["summary_refresh_fallback_used"], 1)
        self.assertTrue(self.conversation.context_summary)

    def test_history_changed_before_summary_rejects_old_context(self):
        snapshot = self.repo.history_snapshot(self.conversation.id, exclude_message_id=self.assistant.id)
        self.service.delete_message(self.rows[0].id, self.conversation.id)
        with self.assertRaises(HTTPException) as caught:
            self._refresh(snapshot=snapshot)
        self.assertEqual(caught.exception.status_code, 409)
        self.assert_summary_cleared()

    def test_late_summary_cannot_restore_deleted_history(self):
        mid, cid = self.rows[0].id, self.conversation.id
        async def summarize(**kwargs):
            self.assertFalse(self.db.in_transaction())
            with Session(self.engine, autoflush=False) as other:
                if self.engine.dialect.name == "postgresql":
                    other.execute(text("set local lock_timeout = '1s'"))
                MessageService(MessageRepository(other)).delete_message(mid, cid)
            return "包含已删除内容的迟到摘要"
        with patch("app.services.chat_context_assembly_service.ChatProviderService.complete_chat", side_effect=summarize):
            with self.assertRaises(HTTPException) as caught:
                self._refresh()
        self.assertEqual(caught.exception.status_code, 409)
        self.assert_summary_cleared()

    def test_same_id_content_edit_is_detected_despite_cached_orm(self):
        mid, cid = self.rows[0].id, self.conversation.id
        async def summarize(**kwargs):
            with Session(self.engine, autoflush=False) as other:
                repo = MessageRepository(other)
                conversation = repo.lock_conversation(cid)
                repo.invalidate_summary_for_changes(conversation, [mid])
                other.get(Message, mid).content = "同 ID 新内容"
                other.commit()
            return "过期摘要"
        with patch("app.services.chat_context_assembly_service.ChatProviderService.complete_chat", side_effect=summarize):
            with self.assertRaises(HTTPException):
                self._refresh()
        self.assert_summary_cleared()

    def test_generation_changed_during_summary_rejects_old_request(self):
        aid, cid = self.assistant.id, self.conversation.id
        async def summarize(**kwargs):
            with Session(self.engine, autoflush=False) as other:
                repo = MessageRepository(other)
                repo.lock_conversation(cid)
                repo.lock_message(aid, cid).generation_id = str(uuid4())
                other.commit()
            return "旧请求的摘要"
        with patch("app.services.chat_context_assembly_service.ChatProviderService.complete_chat", side_effect=summarize):
            with self.assertRaises(HTTPException) as caught:
                self._refresh()
        self.assertEqual(caught.exception.status_code, 409)
        self.assertEqual(self.conversation.context_summary, "已压缩的旧历史")


class ContextSummaryLifecycleTest(SummaryLifecycleCases, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(cls.engine)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()


@unittest.skipUnless(os.getenv("TEST_POSTGRES_URL"), "需要 TEST_POSTGRES_URL")
class ContextSummaryPostgresTest(SummaryLifecycleCases, unittest.TestCase):
    def test_delete_waits_for_summary_commit_then_invalidates_new_summary(self):
        """验证返回后到组装提交前，删除不能越过会话行锁。"""
        mid, cid = self.rows[0].id, self.conversation.id
        with patch("app.services.chat_context_assembly_service.ChatProviderService.complete_chat",
                   new=AsyncMock(return_value="新摘要")):
            self._refresh()
        with Session(self.engine, autoflush=False) as other:
            other.execute(text("set local lock_timeout = '100ms'"))
            with self.assertRaises(DBAPIError):
                MessageService(MessageRepository(other)).delete_message(mid, cid)
        self.db.commit()
        with Session(self.engine, autoflush=False) as other:
            self.assertTrue(MessageService(MessageRepository(other)).delete_message(mid, cid))
        self.assert_summary_cleared()

    @classmethod
    def setUpClass(cls):
        source = make_url(os.environ["TEST_POSTGRES_URL"])
        cls.database_name = f"aiws_summary_test_{uuid4().hex}"
        cls.admin = create_engine(source.set(database="postgres"), isolation_level="AUTOCOMMIT")
        with cls.admin.connect() as conn:
            conn.execute(text(f'create database "{cls.database_name}"'))
        cls.engine = create_engine(source.set(database=cls.database_name))
        with cls.engine.begin() as conn:
            conn.execute(text("create extension if not exists vector"))
        Base.metadata.create_all(cls.engine)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        with cls.admin.connect() as conn:
            conn.execute(text(f'drop database "{cls.database_name}" with (force)'))
        cls.admin.dispose()
