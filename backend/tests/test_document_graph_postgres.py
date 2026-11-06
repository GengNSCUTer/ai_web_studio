"""文档原生图的阶段恢复、审批线程隔离与独立进程宕机验证。"""

import asyncio
from datetime import timedelta
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import sessionmaker

from backend.tests import test_document_tasks_postgres as fixtures
from app.models.agent_runtime import AgentRun, AgentStep, AgentArtifact, AgentOutboxEvent, AgentApproval
from app.models.project_file import ProjectFile
from app.services.agent_runtime_service import AgentRuntimeService
from app.services.document_task_service import DocumentTaskService, DocumentTaskError
from app.services.durable_tool_runtime import DurableToolWorker
from app.services.durable_tool_runtime import DurableToolRunService
from app.services.durable_tool_runtime import utcnow


BODY = "# 确定性草稿\n\nPostgreSQL保存事实，Redis投递。文档图验收。"


def document_process(url, upload_root, window):
    """只在隔离库和临时目录强退，外部结果审计独立提交以计数。"""
    engine = create_engine(url)
    factory = sessionmaker(engine, expire_on_commit=False)
    from app.services import document_task_worker as worker_module
    async def model(**kwargs):
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO graph_test_model_requests DEFAULT VALUES"))
        return BODY
    original = worker_module.commit_document_result
    def commit(worker, db, claim, prepared, result, **kwargs):
        if window == "before_commit":
            # 已得到外部结果，但业务事务尚未提交。
            prepared[0].save_stage(prepared[3], prepared[2], prepared[1], result, transition=kwargs.get("transition"))
            os._exit(83)
        done = original(worker, db, claim, prepared, result, **kwargs)
        if window == "after_commit":
            os._exit(83)
        return done
    worker_module.commit_document_result = commit
    with patch("app.services.document_task_worker.ChatProviderService.complete_chat", side_effect=model), \
         patch("app.services.document_task_service.settings", type("Config", (), {"upload_dir": upload_root})()):
        asyncio.run(DurableToolWorker(session_factory=factory).run_once())


@unittest.skipUnless(os.getenv("TEST_POSTGRES_URL"), "需要隔离PostgreSQL环境")
class DocumentGraphPostgresTest(unittest.TestCase):
    create = fixtures.DocumentTasksPostgresTest.create

    def setUp(self):
        fixtures.DocumentTasksPostgresTest.setUp(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.upload_patch = patch("app.services.document_task_service.settings", type("Config", (), {"upload_dir": self.tmp.name})())
        self.upload_patch.start()
        self.model_patch = patch("app.services.document_task_worker.ChatProviderService.complete_chat", new_callable=AsyncMock)
        self.model = self.model_patch.start()
        async def model(**kwargs):
            with self.engine.begin() as conn:
                conn.execute(text("INSERT INTO graph_test_model_requests DEFAULT VALUES"))
            return BODY
        self.model.side_effect = model
        with self.engine.begin() as conn:
            conn.execute(text("CREATE TABLE graph_test_model_requests(id bigserial PRIMARY KEY)"))

    def tearDown(self):
        self.model_patch.stop()
        self.upload_patch.stop()
        self.tmp.cleanup()
        fixtures.DocumentTasksPostgresTest.tearDown(self)

    def advance(self, count=1):
        async def work():
            worker = DurableToolWorker(session_factory=self.sessions)
            for _ in range(count):
                self.assertTrue(await worker.run_once())
        asyncio.run(work())

    def snapshot(self, rid):
        with self.sessions() as db:
            return DocumentTaskService(db).snapshot("doc-user", rid)

    def prepare_save(self, rid):
        with self.sessions() as db:
            result = DocumentTaskService(db).request_workspace_save("doc-user", rid)
            return result["approvals"][0]["id"]

    def approve(self, aid):
        with self.sessions() as db:
            token = AgentRuntimeService(db).issue_approval_challenge(approval_id=aid, user_id="doc-user")
        with self.sessions() as db:
            return DocumentTaskService(db).approve("doc-user", aid, token)

    def crash(self, window):
        proc = multiprocessing.get_context("spawn").Process(target=document_process,
            args=(self.engine.url.render_as_string(hide_password=False), self.tmp.name, window))
        proc.start()
        proc.join(40)
        if proc.is_alive():
            proc.kill()
            proc.join(5)
        self.assertEqual(proc.exitcode, 83)

    def test_native_stage_graphs_complete_before_optional_approval_and_download_stays_available(self):
        rid = self.create("native-document")
        self.advance(3)
        snapshot = self.snapshot(rid)
        self.assertEqual(snapshot["status"], "succeeded")
        self.assertEqual(snapshot["approvals"], [])
        self.assertEqual(snapshot["workspace_save_status"], "planned")
        self.assertTrue(snapshot["artifacts"][-1]["download_url"])
        with self.sessions() as db:
            self.assertEqual(db.execute(text("SELECT count(*) FROM aiws_graph.runtime_threads")).scalar(), 3)
            self.assertEqual(db.query(ProjectFile).count(), 1)
            states = db.execute(text("SELECT checkpoint FROM aiws_graph.checkpoints")).all()
            self.assertNotIn(BODY, str(states))
            self.assertNotIn("data_base64", str(states))

    def test_save_approval_and_execution_have_separate_threads_for_same_delivery_step(self):
        rid = self.create("save-two-graphs")
        self.advance(3)
        aid = self.prepare_save(rid)
        self.approve(aid)
        self.advance()
        with self.sessions() as db:
            step = db.scalar(select(AgentStep).where(AgentStep.run_id == rid, AgentStep.call_id == "delivery"))
            roles = db.execute(text("SELECT runtime_key FROM aiws_graph.runtime_threads WHERE step_id=:sid"), {"sid": step.id}).scalars().all()
            self.assertEqual(set(roles), {"langgraph_document_v1", "langgraph_document_approval_v1"})
            generated = db.scalar(select(ProjectFile).where(ProjectFile.kind == "generated_document"))
            self.assertTrue((Path(self.tmp.name) / generated.storage_key).is_file())
            self.assertEqual(db.query(AgentArtifact).count(), 4)
        self.assertEqual(self.snapshot(rid)["workspace_save_status"], "succeeded")

    def test_waiting_approval_is_idempotent_and_rejected_save_can_be_requested_again(self):
        rid = self.create("renew-approval")
        self.advance(3)
        aid = self.prepare_save(rid)
        self.assertEqual(self.prepare_save(rid), aid)
        with self.sessions() as db:
            first_version = db.get(AgentRun, rid).state_version
            checkpoint = db.execute(text("SELECT checkpoint FROM aiws_graph.checkpoints ORDER BY checkpoint_id DESC LIMIT 1")).scalar()
            self.assertEqual(checkpoint["channel_values"]["phase"], "waiting_approval")
            DocumentTaskService(db).reject_workspace_save("doc-user", aid)
        self.assertEqual(self.snapshot(rid)["status"], "succeeded")
        self.assertEqual(self.prepare_save(rid), aid)
        with self.sessions() as db:
            self.assertGreater(db.get(AgentRun, rid).state_version, first_version)
        self.approve(aid)
        self.advance()
        self.assertEqual(self.snapshot(rid)["workspace_save_status"], "succeeded")

    def test_invalid_token_and_cross_user_cannot_resume_approval(self):
        rid = self.create("approval-scope")
        self.advance(3)
        aid = self.prepare_save(rid)
        with self.sessions() as db:
            with self.assertRaises(DocumentTaskError):
                DocumentTaskService(db).approve("other-user", aid, "invalid")
            db.rollback()
            with self.assertRaises(DocumentTaskError):
                DocumentTaskService(db).approve("doc-user", aid, "invalid")
        self.assertEqual(self.snapshot(rid)["status"], "waiting_approval")
        with self.sessions() as db:
            self.assertEqual(db.query(ProjectFile).count(), 1)

    def test_expired_pending_interrupt_is_renewed_without_reusing_old_approval_token(self):
        rid = self.create("expired-pending-save")
        self.advance(3)
        aid = self.prepare_save(rid)
        with self.sessions() as db:
            old_token = AgentRuntimeService(db).issue_approval_challenge(approval_id=aid, user_id="doc-user")
            db.get(AgentApproval, aid).expires_at = utcnow() - timedelta(seconds=1)
            db.commit()
        self.assertEqual(self.prepare_save(rid), aid)
        with self.sessions() as db:
            with self.assertRaises(DocumentTaskError):
                DocumentTaskService(db).approve("doc-user", aid, old_token)
        self.assertEqual(self.snapshot(rid)["status"], "waiting_approval")
        self.approve(aid)
        self.advance()
        self.assertEqual(self.snapshot(rid)["workspace_save_status"], "succeeded")

    def test_committed_draft_then_process_exit_only_continues_export(self):
        rid = self.create("draft-commit-crash")
        self.advance()
        self.crash("after_commit")
        self.advance()
        self.assertEqual(self.snapshot(rid)["status"], "succeeded")
        with self.sessions() as db:
            self.assertEqual(db.execute(text("SELECT count(*) FROM graph_test_model_requests")).scalar(), 1)
            self.assertEqual(db.query(AgentArtifact).count(), 3)

    def test_uncommitted_model_result_can_repeat_but_final_draft_is_single(self):
        rid = self.create("draft-before-commit")
        self.advance()
        self.crash("before_commit")
        # 故障进程未完成业务提交，显式将测试事件租约过期，避免实际等待整段租约。
        with self.engine.begin() as conn:
            conn.execute(text("UPDATE agent_outbox_events SET lease_expires_at=now()-interval '1 second' WHERE status='running'"))
        self.advance(2)
        self.assertEqual(self.snapshot(rid)["status"], "succeeded")
        with self.sessions() as db:
            self.assertEqual(db.execute(text("SELECT count(*) FROM graph_test_model_requests")).scalar(), 2)
            self.assertEqual(db.query(AgentArtifact).filter_by(artifact_type="document_draft").count(), 1)

    def test_delivery_commit_before_graph_record_is_not_repeated(self):
        rid = self.create("delivery-commit-crash")
        self.advance(3)
        aid = self.prepare_save(rid)
        self.approve(aid)
        self.crash("after_commit")
        self.assertEqual(self.snapshot(rid)["workspace_save_status"], "succeeded")
        with self.sessions() as db:
            DocumentTaskService(db).approve("doc-user", aid, "already-consumed")
            self.assertEqual(db.query(ProjectFile).filter_by(kind="generated_document").count(), 1)
        self.assertFalse(asyncio.run(DurableToolWorker(session_factory=self.sessions).run_once()))

    def test_unknown_and_unmarked_versions_refuse_execution(self):
        rid = self.create("legacy-doc")
        with self.sessions() as db:
            db.get(AgentRun, rid).planner_state_json = "{}"
            db.commit()
        self.advance()
        self.assertEqual(self.snapshot(rid)["status"], "failed")
        other = self.create("unknown-doc")
        with self.sessions() as db:
            db.get(AgentRun, other).planner_state_json = json.dumps({"runtime_engine": "unknown"})
            db.commit()
        self.advance()
        self.assertEqual(self.snapshot(other)["status"], "failed")

    def test_cancel_after_export_keeps_download_and_blocks_pending_save(self):
        rid = self.create("cancel-save")
        self.advance(3)
        aid = self.prepare_save(rid)
        with self.sessions() as db:
            DocumentTaskService(db).cancel("doc-user", rid)
            with self.assertRaises(DocumentTaskError):
                DocumentTaskService(db).approve("doc-user", aid, "invalid")
        snapshot = self.snapshot(rid)
        self.assertEqual(snapshot["status"], "cancelled")
        self.assertTrue(snapshot["artifacts"][-1]["download_url"])

    def test_deleting_run_cascades_all_document_graph_threads(self):
        rid = self.create("delete-graphs")
        self.advance(3)
        self.prepare_save(rid)
        with self.sessions() as db:
            db.delete(db.get(AgentRun, rid))
            db.commit()
            self.assertEqual(db.execute(text("SELECT count(*) FROM aiws_graph.runtime_threads")).scalar(), 0)
            self.assertEqual(db.execute(text("SELECT count(*) FROM aiws_graph.checkpoints")).scalar(), 0)

    def test_late_duplicate_event_cannot_reopen_completed_stage(self):
        rid = self.create("late-event")
        self.advance()
        with self.sessions() as db:
            evidence = db.scalar(select(AgentStep).where(AgentStep.run_id == rid, AgentStep.call_id == "evidence"))
            event = db.scalar(select(AgentOutboxEvent).where(AgentOutboxEvent.step_id == evidence.id))
            event.status = "pending"
            db.commit()
        self.advance(2)
        with self.sessions() as db:
            self.assertEqual(db.get(AgentStep, evidence.id).attempts, 1)
            self.assertEqual(db.query(AgentArtifact).count(), 3)
        self.assertEqual(self.snapshot(rid)["status"], "succeeded")

    def test_second_event_cannot_steal_unexpired_step_lease(self):
        rid = self.create("duplicate-claim")
        with self.sessions() as db:
            first = DurableToolRunService(db).claim_next(owner="worker-a", runtime_kinds=("document_workflow",))
            db.add(AgentOutboxEvent(run_id=rid, step_id=first.step_id, event_key="independent-duplicate", status="pending"))
            db.commit()
        with self.sessions() as db:
            self.assertIsNone(DurableToolRunService(db).claim_next(owner="worker-b", runtime_kinds=("document_workflow",)))
            self.assertEqual(db.get(AgentStep, first.step_id).lease_owner, "worker-a")
