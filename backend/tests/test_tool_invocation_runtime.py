"""共享执行语义的边界测试，直接检查事务/租户/审批，不复刻实现分支。"""

from copy import deepcopy
import asyncio
from datetime import timedelta
import hashlib
import json
from types import SimpleNamespace
import unittest

from sqlalchemy import select

from app.models.agent_runtime import AgentArtifact, AgentRun, AgentStep
from app.services.runtime_lease import utcnow
from app.services.tool_invocation_runtime import (
    InvocationIdentity, InvocationOutcome, InvocationRecoveryPolicy, InvocationRuntimeError, ToolInvocationRuntime,
)
from app.services.tools.schemas import ExternalSource, PlannedToolCall, ToolCallResult, ToolDefinition, tool_execution_snapshot
from backend.tests import test_durable_tool_runtime as durable


class InvocationPolicyTest(unittest.TestCase):
    def test_approval_preview_never_unlocks_dependency(self):
        call = PlannedToolCall("x", "test", "test", "test", "test", 1, "test")
        for status, semantics, quality, expected in [
            ("success", "evidence", "valid", "succeeded"),
            ("success", "empty_answer", "valid", "succeeded"),
            ("success", "approval_draft", "valid", "preview"),
            ("confirmation_required", "approval_draft", "valid", "waiting_approval"),
            ("success", "evidence", "uncertain", "failed"),
            ("success", "evidence", "invalid", "failed"),
        ]:
            with self.subTest(status=status, semantics=semantics, quality=quality):
                result = ToolCallResult(call, status, [], 0, quality_status=quality, result_semantics=semantics)
                outcome = InvocationOutcome.from_result(result)
                self.assertEqual(outcome.step_status, expected)
                self.assertEqual(outcome.dependency_ready, expected == "succeeded")

    def test_external_adapter_cannot_claim_write_is_replayable(self):
        for read_only, risk, allowed in [(True, "low", True), (True, "high", False), (False, "low", False)]:
            with self.subTest(read_only=read_only, risk=risk):
                definition = ToolDefinition("test", "test", "test", "test", "test", read_only=read_only,
                    risk_level=risk, adapter={"replayable": True, "idempotency_policy": "supported"})
                policy = InvocationRecoveryPolicy.for_tool(definition)
                self.assertEqual(policy.allows_unknown_result_retry, allowed)
                if not allowed:
                    self.assertEqual(policy.reconciliation_policy, "manual_confirmation")

    def test_snapshot_changes_are_rejected(self):
        definition = ToolDefinition("test", "test", "test", "test", "test")
        snapshot = tool_execution_snapshot(definition)
        ToolInvocationRuntime.verify_snapshot(definition, snapshot)
        definition.read_only = False
        with self.assertRaises(InvocationRuntimeError):
            ToolInvocationRuntime.verify_snapshot(definition, snapshot)

    def test_result_payload_keeps_quality_and_redacts_nested_content(self):
        call = PlannedToolCall("x", "test", "test", "test", "test", 1, "test")
        source = ExternalSource("test", "test", "test", "API_KEY=secret-value", metadata={"token": "secret-value"})
        result = ToolCallResult(call, "success", [source], 3, quality_status="valid", result_semantics="empty_answer")
        payload = ToolInvocationRuntime.result_payload(call, result, [])
        self.assertNotIn("secret-value", json.dumps(payload))
        self.assertEqual((payload["quality_status"], payload["result_semantics"]), ("valid", "empty_answer"))


class InvocationJournalTest(unittest.TestCase):
    setUp = durable.DurableToolRuntimeTest.setUp
    tearDown = durable.DurableToolRuntimeTest.tearDown
    _enqueue = durable.DurableToolRuntimeTest._enqueue

    def fixture(self, db):
        run_id = self._enqueue([{"call_id": "a", "tool_key": "workspace.files.list", "arguments": {}}])
        return db.get(AgentRun, run_id), db.scalar(select(AgentStep).where(AgentStep.run_id == run_id))

    def test_attempt_limit_applies_after_process_interruption(self):
        with self.SessionLocal() as db:
            run, step = self.fixture(db)
            for version in range(1, 4):
                ToolInvocationRuntime.start_attempt(run, step, owner=f"worker-{version}",
                    lease_version=version, lease_expires_at=utcnow() + timedelta(seconds=90))
                db.commit()
            with self.assertRaisesRegex(InvocationRuntimeError, "最大执行"):
                ToolInvocationRuntime.start_attempt(run, step, owner="worker-4", lease_version=4,
                    lease_expires_at=utcnow() + timedelta(seconds=90))
            self.assertEqual(step.attempts, 3)

    def test_artifact_and_step_can_roll_back_together(self):
        with self.SessionLocal() as db:
            run, step = self.fixture(db)
            artifact = ToolInvocationRuntime.create_artifact(db, run=run, step=step, content_json='{"ok":true}')
            artifact_id, step_id = artifact.id, step.id
            step.status = "succeeded"
            db.rollback()
            self.assertIsNone(db.get(AgentArtifact, artifact_id))
            self.assertEqual(db.get(AgentStep, step_id).status, "pending")

    def test_same_call_id_different_run_cannot_reuse_result(self):
        with self.SessionLocal() as db:
            run, step = self.fixture(db)
            artifact = ToolInvocationRuntime.create_artifact(db, run=run, step=step, content_json="{}")
            other = SimpleNamespace(id="other-run", user_id=run.user_id, project_id=run.project_id)
            other_step = SimpleNamespace(**{name: getattr(step, name) for name in
                ("id", "run_id", "call_id", "tool_key", "arguments_hash")})
            other_step.id, other_step.run_id = "other-step", "other-run"
            with self.assertRaises(InvocationRuntimeError):
                ToolInvocationRuntime.validate_artifact(other, other_step, artifact)

    def test_artifact_size_uses_bytes_when_configured(self):
        with self.SessionLocal() as db:
            run, step = self.fixture(db)
            with self.assertRaises(InvocationRuntimeError):
                ToolInvocationRuntime.create_artifact(db, run=run, step=step,
                    content_json='"中文"', max_bytes=5)
            self.assertIsNone(db.scalar(select(AgentArtifact).where(AgentArtifact.run_id == run.id)))

    def test_committed_artifact_has_stable_integrity_and_scope(self):
        with self.SessionLocal() as db:
            run, step = self.fixture(db)
            artifact = ToolInvocationRuntime.create_artifact(db, run=run, step=step, content_json='{"ok":true}')
            db.commit()
            self.assertEqual(artifact.content_hash, hashlib.sha256(artifact.content_json.encode()).hexdigest())
            identity = ToolInvocationRuntime.validate_artifact(run, step, artifact)
            self.assertEqual(identity, InvocationIdentity.from_step(run, step))
            other = deepcopy(artifact)
            other.user_id = "foreign-user"
            with self.assertRaises(InvocationRuntimeError):
                ToolInvocationRuntime.validate_artifact(run, step, other)

    def test_exhausted_durable_step_does_not_send_fourth_request(self):
        with self.SessionLocal() as db:
            run, step = self.fixture(db)
            step.attempts = step.max_attempts
            step_id = step.id
            db.commit()
        worker = durable.DurableToolWorker(session_factory=self.SessionLocal, executor_factory=durable.SuccessfulExecutor)
        self.assertTrue(asyncio.run(worker.run_once()))
        with self.SessionLocal() as db:
            step = db.get(AgentStep, step_id)
            self.assertEqual((step.attempts, step.status, step.error_code), (3, "dead_letter", "max_attempts_exhausted"))
            self.assertIsNone(db.scalar(select(AgentArtifact).where(AgentArtifact.step_id == step_id)))

    def test_expired_lease_cannot_start_attempt(self):
        with self.SessionLocal() as db:
            run, step = self.fixture(db)
            with self.assertRaises(InvocationRuntimeError):
                ToolInvocationRuntime.start_attempt(run, step, owner="expired", lease_version=1,
                    lease_expires_at=utcnow() - timedelta(seconds=1))
            self.assertEqual(step.attempts, 0)


if __name__ == "__main__":
    unittest.main()
