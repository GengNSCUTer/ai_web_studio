"""真实PG审批暂停/重启/并发与文件CAS；不向框架状态传入正文或令牌。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import multiprocessing
import os
import unittest
from uuid import uuid4

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.core.migrations import upgrade_database
from app.models.agent_runtime import AgentApproval, AgentRun, AgentStep, FileRevision, PatchDraft
from app.models.project import Project
from app.models.project_file import ProjectFile
from app.models.tool_config import WorkspaceAgentPolicy, WorkspaceToolSetting
from app.models.user import User
from app.services.agent_runtime_service import AgentRuntimeError, AgentRuntimeService, utcnow
from app.services.approval_checkpoint_service import ApprovalIdentity, approval_checkpointer, approval_pool_lifespan, _pools
from app.services.durable_checkpoint_service import checkpoint_conninfo
from app.services.agent_graph.approval_nodes import build_approval_graph


def approval_process(url, uid, pid, fid, action, approval_id=None, token=""):
    """独立进程强退，只改变测试隔离库；模拟暂停或业务提交附近宕机。"""
    engine = create_engine(url)
    with sessionmaker(engine, expire_on_commit=False)() as db:
        service = AgentRuntimeService(db)
        if action == "pause":
            service.propose_file_edit(user_id=uid, project_id=pid, call_id="restart",
                file_id=fid, old_string="old value", new_string="new value")
            os._exit(82)
        if action == "before_commit":
            service._checkpoint = lambda *args: os._exit(82)
        elif action == "after_commit":
            original = service._apply_file_edit_business
            def apply(**kwargs):
                original(**kwargs)
                os._exit(82)
            service._apply_file_edit_business = apply
        service.apply_approved_file_edit(approval_id=approval_id, user_id=uid, approval_token=token)


@unittest.skipUnless(os.getenv("TEST_POSTGRES_URL"), "需要隔离PostgreSQL环境")
class ApprovalGraphPostgresTest(unittest.TestCase):
    def setUp(self):
        url = make_url(os.environ["TEST_POSTGRES_URL"])
        self.name = f"aiws_approval_test_{uuid4().hex[:12]}"
        self.admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
        with self.admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{self.name}"'))
        self.engine = create_engine(url.set(database=self.name))
        upgrade_database(self.engine)
        self.factory = sessionmaker(self.engine, expire_on_commit=False)
        with self.factory() as db:
            user = User(email=f"{uuid4().hex}@approval.test", username="审批图测试")
            other = User(email=f"{uuid4().hex}@approval.test", username="其他用户")
            db.add_all([user, other])
            db.flush()
            project = Project(user_id=user.id, name="审批工作区")
            db.add(project)
            db.flush()
            file = ProjectFile(user_id=user.id, project_id=project.id, kind="text", file_name="notes.md",
                               storage_key="graph-test/notes.md", parsed_text="title\nold value\nend")
            db.add(file)
            db.commit()
            self.uid, self.other, self.pid, self.fid = user.id, other.id, project.id, file.id

    def tearDown(self):
        self.engine.dispose()
        with self.admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE "{self.name}" WITH (FORCE)'))
        self.admin.dispose()

    def propose(self):
        with self.factory() as db:
            return AgentRuntimeService(db).propose_file_edit(user_id=self.uid, project_id=self.pid,
                call_id="edit", file_id=self.fid, old_string="old value", new_string="new value")

    def challenge(self, proposal):
        with self.factory() as db:
            return AgentRuntimeService(db).issue_approval_challenge(approval_id=proposal.approval_id, user_id=self.uid)

    def apply(self, proposal, token, **kwargs):
        with self.factory() as db:
            return AgentRuntimeService(db).apply_approved_file_edit(approval_id=proposal.approval_id,
                user_id=self.uid, approval_token=token, **kwargs)

    def graph_state(self, proposal):
        identity = ApprovalIdentity(self.uid, self.pid, proposal.run_id, proposal.step_id, proposal.approval_id, 1)
        with approval_checkpointer(self.engine.url, identity) as saver:
            return build_approval_graph(saver).get_state(identity.config)

    def assert_unchanged(self):
        with self.factory() as db:
            self.assertIn("old value", db.get(ProjectFile, self.fid).parsed_text)
            self.assertEqual(db.query(FileRevision).count(), 1)

    def test_proposal_persists_interrupt_without_mutation_or_worker_event(self):
        proposal = self.propose()
        state = self.graph_state(proposal)
        self.assertEqual(state.next, ("wait_for_user",))
        self.assertTrue(state.tasks[0].interrupts)
        self.assert_unchanged()
        with self.factory() as db:
            self.assertEqual(db.scalar(text("SELECT count(*) FROM agent_outbox_events")), 0)
            self.assertEqual(db.scalar(text("SELECT count(*) FROM agent_approvals")), 1)
        self.assertEqual(self.propose().approval_id, proposal.approval_id)

    def test_authorized_resume_applies_once_and_clears_interrupt(self):
        proposal = self.propose()
        token = self.challenge(proposal)
        result = self.apply(proposal, token, expected_state_version=1)
        self.assertEqual(result.status, "applied")
        self.assertEqual(self.apply(proposal, "duplicate").revision_id, result.revision_id)
        state = self.graph_state(proposal)
        self.assertEqual(state.next, ())
        self.assertEqual(state.values["phase"], "reconciled")
        with self.factory() as db:
            self.assertEqual(db.get(AgentRun, proposal.run_id).status, "succeeded")
            self.assertEqual(db.query(FileRevision).count(), 2)
            data = str(db.execute(text("SELECT checkpoint FROM aiws_graph.checkpoints")).all())
            writes = bytes().join(row[0] for row in db.execute(text("SELECT blob FROM aiws_graph.checkpoint_writes")))
            self.assertNotIn(token, data)
            self.assertNotIn(token.encode(), writes)
            self.assertNotIn(b"old value", writes)
            self.assertNotIn("new value", data)

    def test_paused_graph_returns_connection_to_managed_pool_and_lifespan_closes_it(self):
        key = checkpoint_conninfo(self.engine.url)
        with approval_pool_lifespan(self.engine.url):
            self.propose()
            pool = _pools[key]
            stats = pool.get_stats()
            self.assertEqual(stats["pool_available"], stats["pool_size"])
            self.assertEqual(stats["requests_waiting"], 0)
        self.assertTrue(pool.closed)
        self.assertNotIn(key, _pools)

    def test_reject_and_repeated_reject_do_not_mutate_or_increment_version_again(self):
        proposal = self.propose()
        for _ in range(2):
            with self.factory() as db:
                result = AgentRuntimeService(db).reject_approval(approval_id=proposal.approval_id, user_id=self.uid)
                self.assertEqual(result.status, "rejected")
        self.assert_unchanged()
        with self.factory() as db:
            self.assertEqual(db.get(AgentRun, proposal.run_id).state_version, 2)
        self.assertEqual(self.graph_state(proposal).next, ())

    def test_wrong_user_and_token_never_resume_interrupt(self):
        proposal = self.propose()
        with self.factory() as db:
            with self.assertRaises(AgentRuntimeError):
                AgentRuntimeService(db).apply_approved_file_edit(approval_id=proposal.approval_id,
                    user_id=self.other, approval_token="invalid")
        self.challenge(proposal)
        with self.assertRaisesRegex(AgentRuntimeError, "确认挑战"):
            self.apply(proposal, "invalid")
        self.assertEqual(self.graph_state(proposal).next, ("wait_for_user",))
        self.assert_unchanged()

    def test_expired_challenge_and_stale_version_do_not_resume(self):
        proposal = self.propose()
        token = self.challenge(proposal)
        with self.assertRaisesRegex(AgentRuntimeError, "版本"):
            self.apply(proposal, token, expected_state_version=99)
        with self.factory() as db:
            db.get(AgentApproval, proposal.approval_id).expires_at = utcnow() - timedelta(seconds=1)
            db.commit()
        with self.assertRaisesRegex(AgentRuntimeError, "失效"):
            self.apply(proposal, token)
        self.assertEqual(self.graph_state(proposal).next, ("wait_for_user",))
        self.assert_unchanged()

    def test_current_file_change_becomes_conflict_without_overwrite(self):
        proposal = self.propose()
        token = self.challenge(proposal)
        with self.factory() as db:
            db.get(ProjectFile, self.fid).parsed_text = "concurrent change"
            db.commit()
        with self.assertRaisesRegex(AgentRuntimeError, "文件已变化"):
            self.apply(proposal, token)
        with self.factory() as db:
            self.assertEqual(db.get(ProjectFile, self.fid).parsed_text, "concurrent change")
            self.assertEqual(db.get(AgentRun, proposal.run_id).status, "conflict")
            self.assertEqual(db.query(FileRevision).count(), 1)

    def test_same_content_after_new_revision_still_rejects_old_baseline(self):
        proposal = self.propose()
        token = self.challenge(proposal)
        with self.factory() as db:
            file = db.get(ProjectFile, self.fid)
            file.parsed_text = "intermediate"
            service = AgentRuntimeService(db)
            service._ensure_current_revision(file)
            file.parsed_text = "title\nold value\nend"
            service._ensure_current_revision(file)
            db.commit()
        with self.assertRaisesRegex(AgentRuntimeError, "文件已变化"):
            self.apply(proposal, token)

    def test_revoked_permission_and_disabled_tool_refuse_resume(self):
        proposal = self.propose()
        token = self.challenge(proposal)
        with self.factory() as db:
            policy = WorkspaceAgentPolicy(project_id=self.pid, permission_mode="read_only")
            db.add(policy)
            db.commit()
        with self.assertRaisesRegex(AgentRuntimeError, "权限已变化"):
            self.apply(proposal, token)
        with self.factory() as db:
            db.get(WorkspaceAgentPolicy, self.pid).permission_mode = "ask"
            db.add(WorkspaceToolSetting(project_id=self.pid, tool_key="workspace.files.apply_edit", is_enabled=False))
            db.commit()
        with self.assertRaisesRegex(AgentRuntimeError, "权限已变化"):
            self.apply(proposal, token)
        self.assert_unchanged()

    def test_changed_tool_definition_does_not_write(self):
        proposal = self.propose()
        token = self.challenge(proposal)
        with self.factory() as db:
            run = db.get(AgentRun, proposal.run_id)
            state = json.loads(run.planner_state_json)
            state["tool_snapshot"] = {"changed": True}
            run.planner_state_json = json.dumps(state)
            db.commit()
        with self.assertRaisesRegex(AgentRuntimeError, "工具定义"):
            self.apply(proposal, token)
        self.assert_unchanged()

    def test_tampered_draft_cannot_change_confirmed_arguments(self):
        proposal = self.propose()
        token = self.challenge(proposal)
        with self.factory() as db:
            draft = db.get(PatchDraft, proposal.patch_draft_id)
            draft.new_string = "unexpected value"
            draft.proposed_content_hash = AgentRuntimeService._hash_text("title\nunexpected value\nend")
            db.commit()
        with self.assertRaisesRegex(AgentRuntimeError, "参数已变化"):
            self.apply(proposal, token)
        self.assert_unchanged()

    def test_file_renamed_sensitive_after_preview_cannot_be_written(self):
        proposal = self.propose()
        token = self.challenge(proposal)
        with self.factory() as db:
            db.get(ProjectFile, self.fid).file_name = ".env"
            db.commit()
        with self.assertRaisesRegex(AgentRuntimeError, "敏感级别"):
            self.apply(proposal, token)
        self.assert_unchanged()

    def test_model_or_checkpoint_cannot_resume_without_authenticated_decision(self):
        from langgraph.types import Command
        from app.services.agent_graph.approval_nodes import ApprovalContext
        from app.services.approval_checkpoint_service import ApprovalCheckpointConflict
        proposal = self.propose()
        identity = ApprovalIdentity(self.uid, self.pid, proposal.run_id, proposal.step_id, proposal.approval_id, 1)
        with self.factory() as db, approval_checkpointer(self.engine.url, identity) as saver:
            with self.assertRaises(ApprovalCheckpointConflict):
                build_approval_graph(saver).invoke(Command(resume="approve"), config=identity.config,
                    context=ApprovalContext(AgentRuntimeService(db), identity), durability="sync")
        self.assert_unchanged()

    def test_unknown_and_unmarked_approval_runtime_rejected(self):
        proposal = self.propose()
        token = self.challenge(proposal)
        with self.factory() as db:
            run = db.get(AgentRun, proposal.run_id)
            state = json.loads(run.planner_state_json)
            state["runtime_engine"] = "unknown"
            run.planner_state_json = json.dumps(state)
            db.commit()
        with self.assertRaisesRegex(AgentRuntimeError, "未知"):
            self.apply(proposal, token)
        self.assert_unchanged()
        with self.factory() as db:
            run = db.get(AgentRun, proposal.run_id)
            state.pop("runtime_engine")
            run.planner_state_json = json.dumps(state)
            db.commit()
        with self.assertRaisesRegex(AgentRuntimeError, "缺失"):
            self.apply(proposal, token)
        self.assert_unchanged()

    def test_cancelled_run_cannot_resume(self):
        proposal = self.propose()
        token = self.challenge(proposal)
        with self.factory() as db:
            db.get(AgentRun, proposal.run_id).status = "cancelled"
            db.commit()
        with self.assertRaisesRegex(AgentRuntimeError, "等待"):
            self.apply(proposal, token)
        self.assert_unchanged()

    def test_concurrent_confirmation_creates_one_revision(self):
        proposal = self.propose()
        token = self.challenge(proposal)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: self.apply(proposal, token), range(2)))
        self.assertEqual(results[0].revision_id, results[1].revision_id)
        with self.factory() as db:
            self.assertEqual(db.query(FileRevision).count(), 2)

    def test_pause_survives_independent_process_exit(self):
        proc = multiprocessing.get_context("spawn").Process(target=approval_process,
            args=(self.engine.url.render_as_string(hide_password=False), self.uid, self.pid, self.fid, "pause"))
        proc.start()
        proc.join(30)
        self.assertEqual(proc.exitcode, 82)
        proposal = self.propose()
        self.assertEqual(self.graph_state(proposal).next, ("wait_for_user",))
        self.assertEqual(self.apply(proposal, self.challenge(proposal)).status, "applied")

    def _crash_then_resume(self, window):
        proposal = self.propose()
        token = self.challenge(proposal)
        proc = multiprocessing.get_context("spawn").Process(target=approval_process,
            args=(self.engine.url.render_as_string(hide_password=False), self.uid, self.pid, self.fid,
                  window, proposal.approval_id, token))
        proc.start()
        proc.join(30)
        self.assertEqual(proc.exitcode, 82)
        result = self.apply(proposal, token)
        self.assertEqual(result.status, "applied")
        with self.factory() as db:
            self.assertEqual(db.query(FileRevision).count(), 2)
            self.assertEqual(db.get(AgentRun, proposal.run_id).state_version, 2)

    def test_crash_before_file_transaction_commit_resumes_once(self):
        self._crash_then_resume("before_commit")

    def test_crash_after_file_commit_before_graph_record_does_not_apply_twice(self):
        self._crash_then_resume("after_commit")

    def test_user_can_reject_after_prior_approval_crashes_before_commit(self):
        proposal = self.propose()
        token = self.challenge(proposal)
        proc = multiprocessing.get_context("spawn").Process(target=approval_process,
            args=(self.engine.url.render_as_string(hide_password=False), self.uid, self.pid, self.fid,
                  "before_commit", proposal.approval_id, token))
        proc.start()
        proc.join(30)
        self.assertEqual(proc.exitcode, 82)
        self.assert_unchanged()
        with self.factory() as db:
            result = AgentRuntimeService(db).reject_approval(approval_id=proposal.approval_id, user_id=self.uid)
            self.assertEqual(result.status, "rejected")
        self.assert_unchanged()
        self.assertEqual(self.graph_state(proposal).values["decision"], "reject")
