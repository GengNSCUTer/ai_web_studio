"""Chat专用任务的状态裁决、重放和取消；测试不访问在线Provider。"""

import asyncio
from contextlib import aclosing
from datetime import timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import *  # noqa: F403 - 注册全部业务表
from app.models.chat_run import ChatRun, ChatRunEvent
from app.models.user import User
from app.models.message import Message
from app.schemas.message import ChatStreamRequest, ChatRegenerateRequest
from app.services.chat_execution_models import ChatExecutionContext
from app.services.chat_provider_service import ChatStreamEvent
from app.services.chat_run_service import ChatRunService, ChatClaim
from app.services.chat_run_worker import ChatRunWorker
from app.services.runtime_lease import utcnow
from app.api.routes.chat_runs import subscribe_events


class ChatRunTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.tmp.name) / 'test.db'}", connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False, autoflush=False)
        with self.sessions() as db:
            db.add_all([User(id="u", email="u@test.invalid"), User(id="v", email="v@test.invalid")])
            db.commit()

    def tearDown(self):
        self.engine.dispose()
        self.tmp.cleanup()

    def create(self, key="r1", **values):
        with self.sessions() as db:
            row = ChatRunService(db).create(user=db.get(User, "u"), request_id=key,
                payload=ChatStreamRequest(content="介绍事务", **values))
            return row.id

    def call(self, method, *args, **kwargs):
        with self.sessions() as db:
            return getattr(ChatRunService(db), method)(*args, **kwargs)

    def snapshot(self, run_id):
        with self.sessions() as db:
            return ChatRunService.snapshot(db.get(ChatRun, run_id))

    def test_duplicate_create_reuses_messages_and_rejects_changed_input(self):
        run = self.create()
        self.assertEqual(self.create(), run)
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Message)), 2)
            with self.assertRaises(HTTPException) as cm:
                ChatRunService(db).create(user=db.get(User, "u"), request_id="r1", payload=ChatStreamRequest(content="其他"))
            self.assertEqual(cm.exception.status_code, 409)

    def test_account_quota_counts_background_runs_not_subscribers(self):
        self.create("1")
        self.create("2")
        with self.assertRaises(HTTPException) as cm:
            self.create("3")
        self.assertEqual(cm.exception.status_code, 429)

    def test_create_failure_rolls_back_entire_turn(self):
        with patch.object(ChatRunService, "_event", side_effect=RuntimeError("controlled")):
            with self.assertRaises(RuntimeError):
                self.create()
        with self.sessions() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(Message)), 0)
            self.assertEqual(db.scalar(select(func.count()).select_from(ChatRun)), 0)

    def test_cancel_queued_is_terminal_and_repeat_is_noop(self):
        run = self.create()
        self.call("cancel", run, "u")
        first = self.snapshot(run)
        self.call("cancel", run, "u")
        self.assertEqual(self.snapshot(run)["last_event_id"], first["last_event_id"])
        self.assertIsNone(self.call("claim_next", "worker"))
        self.assertEqual(first["status"], "cancelled")

    def test_cancel_wins_over_late_completion_and_completion_wins_over_late_cancel(self):
        first = self.create()
        claim = self.call("claim_next", "worker")
        self.call("append", claim, [{"type": "answer_delta", "text": "部分"}])
        self.call("cancel", first, "u")
        self.assertFalse(self.call("append", claim, [{"type": "answer_delta", "text": "迟到"}]))
        self.call("finish", claim, status="succeeded")
        self.assertEqual(self.snapshot(first)["status"], "cancelled")
        second = self.create("second")
        claim = self.call("claim_next", "worker")
        self.call("finish", claim, status="succeeded", content="完整")
        count = self.snapshot(second)["last_event_id"]
        self.call("cancel", second, "u")
        self.assertEqual(self.snapshot(second)["status"], "succeeded")
        self.assertEqual(self.snapshot(second)["last_event_id"], count)

    def test_lease_expiry_preserves_partial_without_retry_or_late_write(self):
        run = self.create()
        claim = self.call("claim_next", "A")
        self.call("start_generation", claim)
        self.call("append", claim, [{"type": "answer_delta", "text": "已落库"}])
        with self.sessions() as db:
            db.get(ChatRun, run).lease_expires_at = utcnow() - timedelta(seconds=1)
            db.commit()
        self.assertIsNone(self.call("claim_next", "B"))
        self.assertFalse(self.call("finish", claim, status="succeeded", content="覆盖"))
        snap = self.snapshot(run)
        self.assertEqual((snap["status"], snap["content"], snap["model_attempts"]), ("interrupted", "已落库", 1))

    def test_cross_user_read_and_cancel_are_denied(self):
        run = self.create()
        for method in ("owned", "cancel"):
            with self.assertRaises(HTTPException) as cm:
                self.call(method, run, "v")
            self.assertEqual(cm.exception.status_code, 404)

    def test_generation_superseded_is_fenced(self):
        run = self.create()
        claim = self.call("claim_next", "A")
        with self.sessions() as db:
            db.get(Message, db.get(ChatRun, run).assistant_message_id).generation_id = "new"
            db.commit()
        self.assertFalse(self.call("append", claim, [{"type": "answer_delta", "text": "旧"}]))
        self.assertEqual(self.snapshot(run)["error_code"], "generation_superseded")

    def test_queue_expiry_and_old_owner_version_are_rejected(self):
        first = self.create()
        with self.sessions() as db:
            db.get(ChatRun, first).queued_expires_at = utcnow() - timedelta(seconds=1)
            db.commit()
        self.assertIsNone(self.call("claim_next", "A"))
        self.assertEqual(self.snapshot(first)["status"], "expired")
        second = self.create("second")
        claim = self.call("claim_next", "B")
        self.assertFalse(self.call("heartbeat", ChatClaim(second, "B", claim.version - 1)))
        self.assertFalse(self.call("finish", ChatClaim(second, "A", claim.version), status="succeeded"))

    def test_global_limit_and_output_budget_preserve_atomicity(self):
        from dataclasses import replace
        from app.core.config import settings
        first, second = self.create("one"), self.create("two")
        with patch("app.services.chat_run_service.settings", replace(settings, chat_run_global_concurrency=1,
                                                                      chat_run_max_chars=8)):
            claim = self.call("claim_next", "A")
            self.assertIsNone(self.call("claim_next", "B"))
            before = self.snapshot(first)["last_event_id"]
            with self.assertRaisesRegex(ValueError, "budget"):
                self.call("append", claim, [{"type": "answer_delta", "text": "x" * 9}])
            self.assertEqual(self.snapshot(first)["last_event_id"], before)
            self.assertEqual(self.snapshot(first)["content"], "")
            self.call("finish", claim, status="failed", error_code="output_budget_exhausted")
            self.assertEqual(self.call("claim_next", "B").run_id, second)

    async def test_cursor_at_terminal_tail_returns_snapshot(self):
        run = self.create()
        claim = self.call("claim_next", "A")
        self.call("finish", claim, status="succeeded", content="完整")
        snap = self.snapshot(run)
        events = [e async for e in subscribe_events(run, "u", after=snap["last_event_id"], sessions=self.sessions)]
        self.assertIn('"type": "run_snapshot"', events[0])
        self.assertIn("完整", events[0])

    async def test_replay_cursor_and_pruned_snapshot_do_not_generate(self):
        run = self.create()
        claim = self.call("claim_next", "A")
        self.call("append", claim, [{"type": "answer_delta", "text": "完整"}])
        self.call("finish", claim, status="succeeded")
        events = [e async for e in subscribe_events(run, "u", after=2, sessions=self.sessions)]
        self.assertIn('"text": "完整"', "".join(events))
        self.assertIn("event: chat", events[0])
        with self.sessions() as db:
            db.get(ChatRun, run).finished_at = utcnow() - timedelta(days=8)
            db.commit()
        self.call("prune_events")
        events = [e async for e in subscribe_events(run, "u", sessions=self.sessions)]
        self.assertIn('"type": "run_snapshot"', events[0])
        self.assertEqual(self.snapshot(run)["model_attempts"], 0)

    def fake_prepare(self):
        async def prepare(service, data):
            return ChatExecutionContext(service.conversation_repo, service.message_service,
                data.conversation, data.user_message, data.assistant_message, [], "test", "openai-compatible",
                "http://not-used.invalid", None, .2, 1, 100, [], {}, {}, None, False, None, [], [],
                generation_id=data.assistant_message.generation_id)
        return patch("app.services.chat_execution_service.ChatExecutionService.prepare_existing_turn_execution", prepare)

    async def test_close_subscription_worker_continues_and_replay_calls_model_once(self):
        run = self.create()
        start, release = asyncio.Event(), asyncio.Event()
        calls = []
        class Provider:
            async def stream_chat_events(self, **kwargs):
                calls.append(kwargs)
                yield ChatStreamEvent("answer_delta", "前100个Token" * 30)
                start.set()
                await release.wait()
                yield ChatStreamEvent("answer_delta", "最终完整内容")
        worker = ChatRunWorker(session_factory=self.sessions, provider_factory=Provider)
        with self.fake_prepare():
            task = asyncio.create_task(worker.run_once())
            await asyncio.wait_for(start.wait(), 5)
            async with aclosing(subscribe_events(run, "u", sessions=self.sessions)) as sub:
                await sub.__anext__()
            release.set()
            await asyncio.wait_for(task, 5)
        events = [e async for e in subscribe_events(run, "u", sessions=self.sessions)]
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.snapshot(run)["status"], "succeeded")
        self.assertTrue(self.snapshot(run)["content"].endswith("最终完整内容"))
        self.assertIn("最终完整内容", "".join(events))

    async def test_stop_cancels_provider_saves_partial_and_allows_new_request(self):
        run = self.create()
        started, closed = asyncio.Event(), asyncio.Event()
        class Provider:
            async def stream_chat_events(self, **kwargs):
                try:
                    yield ChatStreamEvent("answer_delta", "部分内容")
                    started.set()
                    await asyncio.sleep(30)
                finally:
                    closed.set()
        worker = ChatRunWorker(session_factory=self.sessions, provider_factory=Provider)
        worker.HEARTBEAT_SECONDS = .02
        with self.fake_prepare():
            task = asyncio.create_task(worker.run_once())
            await asyncio.wait_for(started.wait(), 5)
            for _ in range(50):
                if (await asyncio.to_thread(self.snapshot, run))["content"]:
                    break
                await asyncio.sleep(.02)
            await asyncio.to_thread(self.call, "cancel", run, "u")
            await asyncio.wait_for(task, 5)
        self.assertTrue(closed.is_set())
        self.assertEqual(self.snapshot(run)["status"], "cancelled")
        self.assertEqual(self.snapshot(run)["content"], "部分内容")
        self.create("after-cancel", conversation_id=self.snapshot(run)["conversation_id"])

    async def test_provider_failure_preserves_partial_and_hides_exception_text(self):
        run = self.create()
        calls = []
        class Provider:
            async def stream_chat_events(self, **kwargs):
                calls.append(1)
                yield ChatStreamEvent("answer_delta", "保留内容")
                raise RuntimeError("secret-private-api-key")
        worker = ChatRunWorker(session_factory=self.sessions, provider_factory=Provider)
        with self.fake_prepare():
            await worker.run_once()
        self.assertEqual(len(calls), 1)
        snap = self.snapshot(run)
        self.assertEqual((snap["status"], snap["content"]), ("failed", "保留内容"))
        events = "".join([e async for e in subscribe_events(run, "u", sessions=self.sessions)])
        self.assertNotIn("secret-private-api-key", events)

    async def test_provider_token_limit_cannot_be_reported_as_complete(self):
        run = self.create()
        class Provider:
            async def stream_chat_events(self, **kwargs):
                yield ChatStreamEvent("answer_delta", "被截断的回答")
                yield ChatStreamEvent("provider_finish", data={"finish_reason": "length"})
        worker = ChatRunWorker(session_factory=self.sessions, provider_factory=Provider)
        with self.fake_prepare():
            await worker.run_once()
        snap = self.snapshot(run)
        self.assertEqual((snap["status"], snap["error_code"], snap["content"]),
                         ("failed", "output_token_limit", "被截断的回答"))

    async def test_cancellation_before_preparation_finishes_never_calls_model(self):
        run = self.create()
        entered = asyncio.Event()
        async def prepare(*args):
            entered.set()
            await asyncio.sleep(30)
        worker = ChatRunWorker(session_factory=self.sessions, provider_factory=lambda: self.fail("不应调用Provider"))
        worker.HEARTBEAT_SECONDS = .02
        with patch("app.services.chat_execution_service.ChatExecutionService.prepare_existing_turn_execution", prepare):
            task = asyncio.create_task(worker.run_once())
            await asyncio.wait_for(entered.wait(), 5)
            await asyncio.to_thread(self.call, "cancel", run, "u")
            await asyncio.wait_for(task, 5)
        self.assertEqual(self.snapshot(run)["status"], "cancelled")
        self.assertEqual(self.snapshot(run)["model_attempts"], 0)

    async def test_deadline_expiry_saves_partial_without_retry(self):
        run = self.create()
        class Provider:
            async def stream_chat_events(self, **kwargs):
                yield ChatStreamEvent("answer_delta", "超时前内容")
                await asyncio.sleep(30)
        worker = ChatRunWorker(session_factory=self.sessions, provider_factory=Provider)
        worker.HEARTBEAT_SECONDS = .02
        with self.fake_prepare():
            task = asyncio.create_task(worker.run_once())
            for _ in range(100):
                if (await asyncio.to_thread(self.snapshot, run))["content"]:
                    break
                await asyncio.sleep(.02)
            self.assertEqual(self.snapshot(run)["content"], "超时前内容")
            # 不依赖100ms墙钟抢跑；确认首片段后设置真实任务截止时间。
            with self.sessions() as db:
                db.get(ChatRun, run).deadline_at = utcnow() - timedelta(seconds=1)
                db.commit()
            await asyncio.wait_for(task, 5)
        self.assertEqual(self.snapshot(run)["status"], "expired")
        self.assertEqual(self.snapshot(run)["content"], "超时前内容")
