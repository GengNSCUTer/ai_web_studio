"""真实PG检查点与业务产物、租约写入、重试和版本兼容。"""

import asyncio
import json
import os
import multiprocessing
import unittest
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from langgraph.runtime import Runtime

from app.core.migrations import upgrade_database
from app.models.agent_runtime import AgentArtifact, AgentOutboxEvent, AgentRun, AgentStep
from app.models.project import Project
from app.models.user import User
from app.models.conversation import Conversation
from app.models.message import Message
from app.services.durable_checkpoint_service import (
    CheckpointIdentity, CheckpointLeaseLost, durable_checkpointer, checkpoint_pool_lifespan,
)
from app.services.durable_tool_runtime import DurableToolRunService, DurableToolWorker, utcnow
from app.services.agent_graph.durable_graph import build_durable_graph, DurableGraphContext
from backend.tests.test_durable_tool_runtime import SuccessfulExecutor, FailingExecutor


def crash_worker(url, window):
    """隔离子进程用真实退出模拟宕机；不把生产服务或其他账号放入故障范围。"""
    engine = create_engine(url)
    factory = sessionmaker(engine, expire_on_commit=False)

    class AuditExecutor(SuccessfulExecutor):
        async def execute(self, call):
            with engine.begin() as conn:
                conn.execute(text("INSERT INTO graph_test_requests(call_id) VALUES (:call)"), {"call": call.call_id})
            return await super().execute(call)

    worker = DurableToolWorker(session_factory=factory, owner="crash-worker", executor_factory=AuditExecutor)
    original_execute = worker._execute_with_heartbeat

    async def execute(executor, call, claim):
        if window == "before_execute":
            os._exit(81)
        result = await original_execute(executor, call, claim)
        if window == "after_execute":
            os._exit(81)
        return result

    original_finish = worker._finish_success

    def finish(*args, **kwargs):
        original_finish(*args, **kwargs)
        if window == "after_commit":
            os._exit(81)

    worker._execute_with_heartbeat = execute
    worker._finish_success = finish
    if window == "before_message":
        def stop_before_projection(db, run):
            os._exit(81)
        DurableToolWorker._project_terminal_message = staticmethod(stop_before_projection)
    asyncio.run(worker.run_once())


@unittest.skipUnless(os.getenv("TEST_POSTGRES_URL"), "需要隔离PostgreSQL环境")
class DurableGraphPostgresTest(unittest.TestCase):
    def setUp(self):
        url = make_url(os.environ["TEST_POSTGRES_URL"])
        self.name = f"aiws_graph_test_{uuid4().hex[:12]}"
        self.admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
        with self.admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{self.name}"'))
        self.engine = create_engine(url.set(database=self.name))
        upgrade_database(self.engine)
        self.factory = sessionmaker(self.engine, expire_on_commit=False)
        with self.factory() as db:
            user = User(email=f"{uuid4().hex}@graph.test", username="图测试")
            db.add(user)
            db.flush()
            project = Project(user_id=user.id, name="图工作区")
            db.add(project)
            db.flush()
            conversation = Conversation(user_id=user.id, project_id=project.id, title="图恢复原会话", model_name="test")
            db.add(conversation)
            db.commit()
            self.uid, self.pid, self.cid = user.id, project.id, conversation.id
        with self.engine.begin() as conn:
            conn.execute(text("CREATE TABLE graph_test_requests(id bigserial PRIMARY KEY, call_id text NOT NULL)"))

    def tearDown(self):
        self.engine.dispose()
        with self.admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE "{self.name}" WITH (FORCE)'))
        self.admin.dispose()

    def enqueue(self, calls=None):
        with self.factory() as db:
            run = DurableToolRunService(db).enqueue(user_id=self.uid, project_id=self.pid,
                conversation_id=self.cid, assistant_message_id=None, calls=calls or [
                    {"call_id": "list", "tool_key": "workspace.files.list", "arguments": {}}])
            return run.id

    def identity(self, claim, owner="graph-a"):
        with self.factory() as db:
            step = db.get(AgentStep, claim.step_id)
            return CheckpointIdentity(self.uid, self.pid, step.run_id, step.id,
                                      claim.outbox_event_id, owner, claim.outbox_lease_version)

    def test_native_graph_writes_real_checkpoints_and_business_artifact(self):
        rid = self.enqueue()
        worker = DurableToolWorker(session_factory=self.factory, owner="graph-a", executor_factory=SuccessfulExecutor)
        self.assertTrue(asyncio.run(worker.run_once()))
        with self.factory() as db:
            self.assertEqual(db.get(AgentRun, rid).status, "succeeded")
            self.assertEqual(db.query(AgentArtifact).filter_by(run_id=rid).count(), 1)
            checkpoints = db.execute(text("SELECT checkpoint FROM aiws_graph.checkpoints ORDER BY checkpoint_id DESC")).all()
            self.assertGreaterEqual(len(checkpoints), 4)
            state = checkpoints[0][0]["channel_values"]
            self.assertEqual(state["phase"], "reconciled")
            self.assertEqual(state["status"], "succeeded")
            self.assertNotIn("sources", state)
            self.assertNotIn("arguments", state)

    def test_checkpoint_late_writer_is_rejected_after_new_owner_claims(self):
        self.enqueue()
        with self.factory() as db:
            first = DurableToolRunService(db).claim_next(owner="graph-a")
        identity = self.identity(first)
        with self.factory() as db:
            event = db.get(AgentOutboxEvent, first.outbox_event_id)
            event.lease_expires_at = utcnow() - timedelta(seconds=1)
            db.commit()
            second = DurableToolRunService(db).claim_next(owner="graph-b")
            self.assertGreater(second.outbox_lease_version, first.outbox_lease_version)

        async def check():
            async with durable_checkpointer(self.engine.url, identity) as saver:
                with self.assertRaises(CheckpointLeaseLost):
                    async with saver.conn.transaction():
                        await saver._verify_lease(saver.conn)

        asyncio.run(check())

    def test_expired_but_not_reclaimed_lease_cannot_write_checkpoint(self):
        self.enqueue()
        with self.factory() as db:
            claim = DurableToolRunService(db).claim_next(owner="graph-a")
        identity = self.identity(claim)
        with self.factory() as db:
            db.get(AgentOutboxEvent, claim.outbox_event_id).lease_expires_at = utcnow() - timedelta(seconds=1)
            db.commit()

        async def check():
            async with durable_checkpointer(self.engine.url, identity) as saver:
                with self.assertRaises(CheckpointLeaseLost):
                    await saver.aput_writes(identity.config, [("phase", "late")], "late-task")

        asyncio.run(check())

    def test_cross_user_thread_read_is_rejected_before_database_query(self):
        self.enqueue()
        with self.factory() as db:
            claim = DurableToolRunService(db).claim_next(owner="graph-a")
        identity = self.identity(claim)

        async def check():
            async with durable_checkpointer(self.engine.url, identity) as saver:
                with self.assertRaises(CheckpointLeaseLost):
                    await saver.aget_tuple({"configurable": {"thread_id": "other-user"}})

        asyncio.run(check())

    def test_cancelled_run_does_not_accept_late_checkpoint(self):
        rid = self.enqueue()
        with self.factory() as db:
            claim = DurableToolRunService(db).claim_next(owner="graph-a")
        identity = self.identity(claim)
        with self.factory() as db:
            db.get(AgentRun, rid).status = "cancelled"
            db.commit()

        async def check():
            async with durable_checkpointer(self.engine.url, identity) as saver:
                with self.assertRaises(CheckpointLeaseLost):
                    await saver.aput_writes(identity.config, [("phase", "late")], "late-after-cancel")

        asyncio.run(check())

    def test_retry_under_new_lease_starts_new_graph_without_old_runtime(self):
        rid = self.enqueue()
        worker = DurableToolWorker(session_factory=self.factory, owner="graph-a", executor_factory=FailingExecutor)
        self.assertTrue(asyncio.run(worker.run_once()))
        with self.factory() as db:
            step = db.scalar(select(AgentStep).where(AgentStep.run_id == rid))
            self.assertEqual(step.status, "pending")
            event = db.scalar(select(AgentOutboxEvent).where(AgentOutboxEvent.run_id == rid))
            event.available_at = step.available_at = utcnow() - timedelta(seconds=1)
            db.commit()
        worker = DurableToolWorker(session_factory=self.factory, owner="graph-b", executor_factory=SuccessfulExecutor)
        self.assertTrue(asyncio.run(worker.run_once()))
        with self.factory() as db:
            step = db.scalar(select(AgentStep).where(AgentStep.run_id == rid))
            self.assertEqual(step.status, "succeeded")
            self.assertEqual(step.attempts, 2)
            self.assertEqual(db.query(AgentArtifact).filter_by(run_id=rid).count(), 1)

    def test_unmarked_run_cannot_bypass_graph_version_check(self):
        rid = self.enqueue()
        with self.factory() as db:
            run = db.get(AgentRun, rid)
            state = json.loads(run.planner_state_json)
            state.pop("runtime_engine")
            run.planner_state_json = json.dumps(state)
            db.commit()
        worker = DurableToolWorker(session_factory=self.factory, owner="graph-old", executor_factory=SuccessfulExecutor)
        self.assertTrue(asyncio.run(worker.run_once()))
        with self.factory() as db:
            self.assertEqual(db.get(AgentRun, rid).status, "failed")
            self.assertEqual(db.query(AgentArtifact).filter_by(run_id=rid).count(), 0)
            self.assertEqual(db.execute(text("SELECT count(*) FROM aiws_graph.checkpoints")).scalar(), 0)

    def test_new_lease_after_retry_commit_does_not_resume_old_reconcile_only(self):
        """已提交重排但图停在对账时，新租约必须开始本次执行而非空转90秒。"""
        from unittest.mock import patch
        rid = self.enqueue()
        first = DurableToolWorker(session_factory=self.factory, owner="retry-old", executor_factory=FailingExecutor)
        async def stop(state, runtime: Runtime[DurableGraphContext]):
            raise RuntimeError("模拟对账前退出")
        with patch("app.services.agent_graph.durable_graph._reconcile", new=stop):
            with self.assertRaisesRegex(RuntimeError, "模拟"):
                asyncio.run(first.run_once())
        with self.factory() as db:
            step = db.scalar(select(AgentStep).where(AgentStep.run_id == rid))
            event = db.scalar(select(AgentOutboxEvent).where(AgentOutboxEvent.run_id == rid))
            self.assertEqual(step.status, "pending")
            event.available_at = step.available_at = utcnow() - timedelta(seconds=1)
            db.commit()
        second = DurableToolWorker(session_factory=self.factory, owner="retry-new", executor_factory=SuccessfulExecutor)
        self.assertTrue(asyncio.run(second.run_once()))
        with self.factory() as db:
            step = db.scalar(select(AgentStep).where(AgentStep.run_id == rid))
            self.assertEqual(step.status, "succeeded")
            self.assertEqual(step.attempts, 2)
            self.assertEqual(db.query(AgentArtifact).filter_by(run_id=rid).count(), 1)

    def test_unknown_runtime_version_is_not_executed_as_legacy(self):
        from unittest.mock import Mock
        rid = self.enqueue()
        with self.factory() as db:
            run = db.get(AgentRun, rid)
            state = json.loads(run.planner_state_json)
            state["runtime_engine"] = "future-unsupported"
            run.planner_state_json = json.dumps(state)
            db.commit()
        factory = Mock()
        worker = DurableToolWorker(session_factory=self.factory, executor_factory=factory)
        asyncio.run(worker.run_once())
        factory.assert_not_called()
        with self.factory() as db:
            step = db.scalar(select(AgentStep).where(AgentStep.run_id == rid))
            self.assertEqual(step.error_code, "unsupported_runtime_version")

    def test_managed_pool_is_closed_at_lifespan_exit(self):
        from app.services.durable_checkpoint_service import _pools, checkpoint_conninfo

        async def check():
            loop = asyncio.get_running_loop()
            key = checkpoint_conninfo(self.engine.url)
            async with checkpoint_pool_lifespan(self.engine.url):
                pool = _pools[loop][key]
                self.assertFalse(pool.closed)
                async with checkpoint_pool_lifespan(self.engine.url):
                    self.assertIs(_pools[loop][key], pool)
            self.assertTrue(pool.closed)
            self.assertNotIn(key, _pools[loop])

        asyncio.run(check())

    def crash_and_recover(self, window):
        calls = [{"call_id": "A", "tool_key": "workspace.files.list", "arguments": {}}]
        if window == "after_commit":
            calls.append({"call_id": "B", "tool_key": "workspace.files.list", "arguments": {}, "depends_on": ["A"]})
        rid = self.enqueue(calls)
        process = multiprocessing.get_context("spawn").Process(target=crash_worker,
            args=(self.engine.url.render_as_string(hide_password=False), window))
        process.start()
        process.join(25)
        try:
            self.assertFalse(process.is_alive(), "隔离故障进程未按预期退出")
            self.assertEqual(process.exitcode, 81)
        finally:
            if process.is_alive():
                process.kill()
                process.join(5)
        with self.factory() as db:
            # 真正的独立进程已结束，测试加速租约到期，不等待生产90秒。
            for event in db.scalars(select(AgentOutboxEvent).where(
                AgentOutboxEvent.run_id == rid, AgentOutboxEvent.status == "running")):
                event.lease_expires_at = utcnow() - timedelta(seconds=1)
            for step in db.scalars(select(AgentStep).where(AgentStep.run_id == rid, AgentStep.status == "running")):
                step.lease_expires_at = utcnow() - timedelta(seconds=1)
            db.commit()
        engine = self.engine

        class AuditExecutor(SuccessfulExecutor):
            async def execute(self, call):
                with engine.begin() as conn:
                    conn.execute(text("INSERT INTO graph_test_requests(call_id) VALUES (:call)"), {"call": call.call_id})
                return await super().execute(call)

        worker = DurableToolWorker(session_factory=self.factory, owner="recovery-worker", executor_factory=AuditExecutor)
        for _ in calls:
            asyncio.run(worker.run_once())
        with self.factory() as db:
            self.assertEqual(db.get(AgentRun, rid).status, "succeeded")
            self.assertEqual(db.query(AgentArtifact).filter_by(run_id=rid).count(), len(calls))
            self.assertEqual(db.query(Message).filter_by(conversation_id=self.cid, role="assistant").count(), 1)
            request_count = db.execute(text("SELECT count(*) FROM graph_test_requests WHERE call_id='A'")).scalar()
            expected = 2 if window in {"after_execute", "before_message"} else 1
            self.assertEqual(request_count, expected)
            # 完成Step的Artifact恰好一份；外部响应未提交时，只读请求可按至少一次重做。
            self.assertEqual(db.execute(text("SELECT count(*) FROM aiws_graph.runtime_threads")).scalar(), len(calls))

    def test_process_crash_before_external_execution_resumes(self):
        self.crash_and_recover("before_execute")

    def test_process_crash_after_external_read_before_commit_retries_read_only(self):
        self.crash_and_recover("after_execute")

    def test_process_crash_after_artifact_commit_does_not_repeat_completed_read(self):
        self.crash_and_recover("after_commit")

    def test_process_crash_before_result_message_rolls_back_and_projects_once(self):
        self.crash_and_recover("before_message")

    def test_deleting_run_cascades_native_checkpoint_data(self):
        rid = self.enqueue()
        worker = DurableToolWorker(session_factory=self.factory, executor_factory=SuccessfulExecutor)
        asyncio.run(worker.run_once())
        with self.factory() as db:
            db.delete(db.get(AgentRun, rid))
            db.commit()
            for table in ("runtime_threads", "checkpoints", "checkpoint_blobs", "checkpoint_writes"):
                self.assertEqual(db.execute(text(f"SELECT count(*) FROM aiws_graph.{table}")).scalar(), 0)

    def test_two_live_workers_execute_independent_steps_and_project_one_message(self):
        rid = self.enqueue([
            {"call_id": name, "tool_key": "workspace.files.list", "arguments": {}} for name in ("A", "B")
        ])

        class SlowExecutor(SuccessfulExecutor):
            async def execute(self, call):
                await asyncio.sleep(0.1)
                return await super().execute(call)

        async def execute():
            workers = [DurableToolWorker(session_factory=self.factory, owner=f"parallel-{i}",
                        executor_factory=SlowExecutor) for i in range(2)]
            self.assertEqual(await asyncio.gather(*(worker.run_once() for worker in workers)), [True, True])

        asyncio.run(execute())
        with self.factory() as db:
            self.assertEqual(db.get(AgentRun, rid).status, "succeeded")
            self.assertEqual(db.query(AgentArtifact).filter_by(run_id=rid).count(), 2)
            self.assertEqual(db.query(Message).filter_by(conversation_id=self.cid, role="assistant").count(), 1)

    def test_late_worker_cannot_save_artifact_after_real_takeover(self):
        rid = self.enqueue()

        async def execute():
            entered, release = asyncio.Event(), asyncio.Event()

            class HoldingExecutor(SuccessfulExecutor):
                async def execute(self, call):
                    entered.set()
                    await release.wait()
                    return await super().execute(call)

            first = DurableToolWorker(session_factory=self.factory, owner="first", executor_factory=HoldingExecutor)
            running = asyncio.create_task(first.run_once())
            await entered.wait()
            with self.factory() as db:
                event = db.scalar(select(AgentOutboxEvent).where(AgentOutboxEvent.run_id == rid))
                event.lease_expires_at = utcnow() - timedelta(seconds=1)
                db.commit()
                takeover = DurableToolRunService(db).claim_next(owner="second")
            release.set()
            await running
            with self.factory() as db:
                self.assertEqual(db.query(AgentArtifact).filter_by(run_id=rid).count(), 0)
                step = db.scalar(select(AgentStep).where(AgentStep.run_id == rid))
                self.assertEqual(step.lease_owner, "second")
                self.assertEqual(step.lease_version, takeover.outbox_lease_version)
            second = DurableToolWorker(session_factory=self.factory, owner="second", executor_factory=SuccessfulExecutor)
            with self.factory() as db:
                await second._execute_claim(db, takeover)

        asyncio.run(execute())
        with self.factory() as db:
            self.assertEqual(db.query(AgentArtifact).filter_by(run_id=rid).count(), 1)
            self.assertEqual(db.get(AgentRun, rid).status, "succeeded")

    def test_replay_new_event_never_reuses_old_lease_version(self):
        rid = self.enqueue()
        with self.factory() as db:
            step = db.scalar(select(AgentStep).where(AgentStep.run_id == rid))
            step.max_attempts = 1
            db.commit()
        first = DurableToolWorker(session_factory=self.factory, owner="old", executor_factory=FailingExecutor)
        asyncio.run(first.run_once())
        with self.factory() as db:
            step = db.scalar(select(AgentStep).where(AgentStep.run_id == rid))
            event = db.scalar(select(AgentOutboxEvent).where(AgentOutboxEvent.run_id == rid))
            self.assertEqual(step.status, "dead_letter")
            old_identity = CheckpointIdentity(self.uid, self.pid, rid, step.id, event.id, "old", event.lease_version)
            DurableToolRunService(db).replay_dead_letter(run_id=rid, step_id=step.id, user_id=self.uid)
        second = DurableToolWorker(session_factory=self.factory, owner="new", executor_factory=FailingExecutor)
        asyncio.run(second.run_once())
        with self.factory() as db:
            step = db.scalar(select(AgentStep).where(AgentStep.run_id == rid))
            self.assertEqual(step.status, "dead_letter")
            self.assertGreater(step.lease_version, old_identity.lease_version)

        async def check():
            async with durable_checkpointer(self.engine.url, old_identity) as saver:
                with self.assertRaises(CheckpointLeaseLost):
                    await saver.aput_writes(old_identity.config, [("phase", "stale_after_replay")], "old")

        asyncio.run(check())
