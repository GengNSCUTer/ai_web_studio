"""真实PG及os._exit验证Chat工具图接管；故障仅发生于隔离测试数据库。"""

from collections import Counter
import asyncio
from datetime import timedelta
import json
import multiprocessing
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, select, text, delete, func
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.core.migrations import upgrade_database
from app.models.agent_runtime import AgentArtifact, AgentCheckpoint, AgentRun, AgentStep
from app.models.chat_run import ChatRun
from app.models.conversation import Conversation
from app.models.user import User
from app.services.chat_run_service import ChatClaim, ChatLeaseLost
from app.services.chat_tool_runtime import ChatToolPersistence
from app.services.chat_tool_checkpoint import ChatToolCheckpointIdentity, ChatToolPostgresSaver
from app.services.durable_checkpoint_service import durable_checkpointer, CheckpointLeaseLost
from app.services.runtime_lease import utcnow
from app.services.tools.executor import ToolExecutor
from backend.tests import test_chat_tool_recovery as unit


def crash_child(url, rid, claim, window):
    """运行正式图和真实文件适配器，仅在指定提交窗口终止进程。"""
    engine = create_engine(url)
    harness = unit.ChatToolRecoveryTest()
    harness.sessions = sessionmaker(engine, expire_on_commit=False, autoflush=False)
    persistence = ChatToolPersistence(harness.sessions, claim)
    persistence.initialize()
    original_execute = ToolExecutor._execute
    original_save = persistence.save

    async def execute(executor, call):
        with engine.begin() as db:
            db.execute(text("INSERT INTO chat_test_calls(call_id) VALUES (:call)"), {"call": call.call_id})
        if call.call_id == "B" and window == "inflight_b":
            os._exit(81)
        return await original_execute(executor, call)

    def save(run, next_node):
        if next_node == "observe" and window == "after_artifact":
            os._exit(81)
        original_save(run, next_node)
        if next_node == "plan" and run.round_index == 1 and run.observations and window == "next_plan":
            os._exit(81)

    persistence.save = save
    calls = [harness.tool("A"), harness.tool("B", "workspace.files.list", depends=["A"])]
    planner = unit.FixedPlanner(calls, more=window == "next_plan")
    with patch.object(ToolExecutor, "_execute", execute):
        asyncio.run(harness.context(persistence, planner))


@unittest.skipUnless(os.getenv("TEST_POSTGRES_URL"), "需要真实PG隔离数据库")
class ChatToolRecoveryPostgresTest(unit.ChatToolRecoveryTest):
    @classmethod
    def setUpClass(cls):
        source = make_url(os.environ["TEST_POSTGRES_URL"])
        cls.name = "aiws_chat_tools_" + uuid4().hex[:12]
        cls.admin = create_engine(source.set(database="postgres"), isolation_level="AUTOCOMMIT")
        with cls.admin.connect() as db:
            db.execute(text(f'CREATE DATABASE "{cls.name}"'))
        cls.engine = create_engine(source.set(database=cls.name), pool_size=12, max_overflow=4)
        upgrade_database(cls.engine)
        cls.sessions = sessionmaker(cls.engine, expire_on_commit=False, autoflush=False)
        with cls.engine.begin() as db:
            db.execute(text("CREATE TABLE chat_test_calls(call_id text NOT NULL)"))

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

    def tearDown(self):
        with self.engine.begin() as db:
            db.execute(text("TRUNCATE users CASCADE"))
            db.execute(text("TRUNCATE chat_test_calls"))
            # 此历史配置表没有用户/项目外键，不能假设TRUNCATE users会级联它。
            db.execute(text("TRUNCATE workspace_tool_settings"))
            db.execute(text("TRUNCATE workspace_agent_policies"))

    async def test_real_process_exit_reuses_saved_upstream_and_native_checkpoints(self):
        rid, claim, first = self.fixture()
        process = multiprocessing.get_context("spawn").Process(target=crash_child,
            args=(self.engine.url.render_as_string(hide_password=False), rid, claim, "inflight_b"))
        process.start()
        await asyncio.to_thread(process.join, 30)
        self.assertFalse(process.is_alive())
        self.assertEqual(process.exitcode, 81)
        second = self.expire_and_claim(rid)
        calls = []
        original = ToolExecutor._execute
        async def execute(executor, call):
            calls.append(call.call_id)
            return await original(executor, call)
        planner = unit.FixedPlanner([])
        with patch.object(ToolExecutor, "_execute", execute):
            result = await self.context(second, planner)
        self.assertEqual(calls, ["B"])
        self.assertEqual(planner.count, 0)
        self.assertIn("短事务", result.context_text)
        with self.sessions() as db:
            self.assertEqual(Counter(db.scalars(text("SELECT call_id FROM chat_test_calls"))), {"A": 1, "B": 1})
            native = db.scalar(text("SELECT count(*) FROM aiws_graph.checkpoints"))
            self.assertGreater(native, 0)
            for value in db.scalars(text("SELECT checkpoint FROM aiws_graph.checkpoints")):
                self.assertNotIn("sources", value["channel_values"])
                self.assertNotIn("arguments", value["channel_values"])

    async def test_real_exit_after_artifact_commit_before_node_checkpoint_does_not_reexecute(self):
        rid, claim, first = self.fixture()
        process = multiprocessing.get_context("spawn").Process(target=crash_child,
            args=(self.engine.url.render_as_string(hide_password=False), rid, claim, "after_artifact"))
        process.start()
        await asyncio.to_thread(process.join, 30)
        self.assertEqual(process.exitcode, 81)
        second = self.expire_and_claim(rid)
        with patch.object(ToolExecutor, "_execute", side_effect=AssertionError("不得重做已提交工具")):
            result = await self.context(second, unit.FixedPlanner([]))
        self.assertIn("短事务", result.context_text)

    async def test_stale_owner_native_checkpoint_write_denied(self):
        rid, claim, first = self.fixture()
        await self.context(first, unit.FixedPlanner([self.tool("A")]))
        self.expire_and_claim(rid)
        identity = ChatToolCheckpointIdentity("u", "p", first.agent_id, first.coordinator_id,
            "", claim.owner, claim.version, rid)
        async with durable_checkpointer(self.engine.url, identity, saver_type=ChatToolPostgresSaver) as saver:
            with self.assertRaises(CheckpointLeaseLost):
                await saver.aput_writes(identity.config, [("phase", "old")], "late")

    async def test_delete_conversation_cascades_agent_and_native_threads(self):
        rid, _, first = self.fixture()
        await self.context(first, unit.FixedPlanner([self.tool("A")]))
        self.call("finish", first.claim, status="succeeded")
        with self.sessions() as db:
            db.delete(db.get(Conversation, "c"))
            db.commit()
            self.assertEqual(db.scalar(select(func.count()).select_from(AgentRun)), 0)
            self.assertEqual(db.scalar(select(func.count()).select_from(AgentArtifact)), 0)
            self.assertEqual(db.scalar(text("SELECT count(*) FROM aiws_graph.runtime_threads")), 0)
