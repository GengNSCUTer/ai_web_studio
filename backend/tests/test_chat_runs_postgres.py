"""真实PostgreSQL验证任务创建/取消竞争、子进程退出和提交后重放。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import os
import subprocess
import sys
import threading
import unittest
from uuid import uuid4

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.core.migrations import upgrade_database
from app.models.chat_run import ChatRun
from app.models.message import Message
from app.models.user import User
from app.repositories.setting_repo import UserSettingRepository
from app.services.chat_run_service import ChatRunService
from app.services.runtime_lease import utcnow
from app.services.setting_service import SettingService
from backend.tests import test_chat_runs as unit


@unittest.skipUnless(os.getenv("TEST_POSTGRES_URL"), "需要真实PG隔离数据库")
class ChatRunPostgresTest(unit.ChatRunTest):
    @classmethod
    def setUpClass(cls):
        source = make_url(os.environ["TEST_POSTGRES_URL"])
        cls.name = "aiws_chat_runs_" + uuid4().hex[:12]
        cls.admin = create_engine(source.set(database="postgres"), isolation_level="AUTOCOMMIT")
        with cls.admin.connect() as db:
            db.execute(text(f'CREATE DATABASE "{cls.name}"'))
        cls.engine = create_engine(source.set(database=cls.name), pool_size=12, max_overflow=4)
        upgrade_database(cls.engine)
        cls.sessions = sessionmaker(cls.engine, expire_on_commit=False, autoflush=False)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        with cls.admin.connect() as db:
            db.execute(text(f'DROP DATABASE "{cls.name}" WITH (FORCE)'))
        cls.admin.dispose()

    def setUp(self):
        with self.sessions() as db:
            db.add_all([User(id="u", email="u@test.invalid"), User(id="v", email="v@test.invalid")])
            db.commit()
            SettingService(UserSettingRepository(db)).get_or_create_user_settings("u")

    def tearDown(self):
        with self.engine.begin() as db:
            db.execute(text("TRUNCATE users CASCADE"))

    def test_concurrent_same_request_creates_exactly_one_turn(self):
        barrier = threading.Barrier(6)
        def submit(_):
            barrier.wait()
            return self.create("same")
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(submit, range(6)))
        self.assertEqual(len(set(results)), 1)
        with self.sessions() as db:
            self.assertEqual(len(list(db.scalars(select(Message)))), 2)

    def test_multiple_workers_cannot_claim_same_run(self):
        run = self.create()
        with ThreadPoolExecutor(max_workers=6) as pool:
            claims = list(pool.map(lambda i: self.call("claim_next", f"w{i}"), range(6)))
        self.assertEqual([claim.run_id for claim in claims if claim], [run])

    def test_cancel_and_completion_concurrent_are_consistent(self):
        run = self.create()
        claim = self.call("claim_next", "worker")
        self.call("append", claim, [{"type": "answer_delta", "text": "部分"}])
        barrier = threading.Barrier(2)
        def finish():
            barrier.wait()
            return self.call("finish", claim, status="succeeded", content="完整")
        def cancel():
            barrier.wait()
            return self.call("cancel", run, "u")
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(finish), pool.submit(cancel)]
            for job in jobs:
                job.result(10)
        snap = self.snapshot(run)
        self.assertIn((snap["status"], snap["content"]), (("succeeded", "完整"), ("cancelled", "部分")))
        with self.sessions() as db:
            message = db.get(Message, snap["assistant_message_id"])
            self.assertEqual(message.content, snap["content"])
            self.assertEqual(message.status, "done" if snap["status"] == "succeeded" else "cancelled")

    def test_process_dies_after_partial_commit_requeues_before_restart_generation(self):
        run = self.create()
        env = {**os.environ, "TEST_CHAT_DATABASE": self.engine.url.render_as_string(hide_password=False),
               "TEST_CHAT_RUN": run}
        code = '''
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.services.chat_run_service import ChatRunService
engine=create_engine(os.environ["TEST_CHAT_DATABASE"])
with sessionmaker(engine)() as db:
 service=ChatRunService(db)
 claim=service.claim_next("dead-worker")
 service.start_generation(claim)
 service.append(claim,[{"type":"answer_delta","text":"崩溃前已保存"}])
os._exit(17)
'''
        result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 17, result.stderr.decode()[-500:])
        with self.sessions() as db:
            db.get(ChatRun, run).lease_expires_at = utcnow() - timedelta(seconds=1)
            db.commit()
        second = self.call("claim_next", "new-worker")
        self.assertIsNotNone(second)
        snap = self.snapshot(run)
        self.assertEqual((snap["status"], snap["content"], snap["model_attempts"]),
                         ("running", "崩溃前已保存", 1))
        self.assertTrue(self.call("start_generation", second))
        self.assertEqual((self.snapshot(run)["content"], self.snapshot(run)["model_attempts"]), ("", 2))

    async def test_uncancellable_adapter_late_result_is_rejected_by_real_executor(self):
        from backend.tests import test_tool_executor as fixtures
        from app.services.chat_run_guard import current_chat_guard
        from app.services.chat_run_worker import ChatRunWorker
        from app.services.tools.catalog import ToolCatalog
        from app.services.tools.schemas import PlannedToolCall

        run = self.create()
        claim = self.call("claim_next", "A")
        worker = ChatRunWorker(session_factory=self.sessions, owner="A")
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []

        class SlowAdapter(fixtures.FakeAdapterRunner):
            async def run(adapter, **kwargs):
                calls.append(1)
                entered.set()
                # 模拟外部调用已经送达，无法撤回；取消只改变数据库任务执行权。
                await release.wait()
                return await super().run(**kwargs)

        executor = fixtures.ToolExecutor(catalog=ToolCatalog(), adapter_runner=SlowAdapter(),
                                        credential_resolver=fixtures.FakeCredentialResolver())
        call = PlannedToolCall(call_id="late", tool_key="web.tavily.search", provider="tavily",
            category="web_search", display_name="搜索", confidence=1, reason="受控迟到测试", arguments={"query": "public"})

        async def execute():
            token = current_chat_guard.set(lambda: worker._guard(claim))
            try:
                return await executor.execute(call)
            finally:
                current_chat_guard.reset(token)

        task = asyncio.create_task(execute())
        await asyncio.wait_for(entered.wait(), 5)
        await asyncio.to_thread(self.call, "cancel", run, "u")
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(task, 5)
        self.call("finish", claim, status="cancelled")
        self.assertEqual(calls, [1])
        self.assertEqual(self.snapshot(run)["content"], "")
