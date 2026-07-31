from __future__ import annotations

import asyncio
import unittest
from datetime import timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.agent_runtime import AgentArtifact, AgentCheckpoint, AgentOutboxEvent, AgentRun, AgentStep
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.project import Project
from app.models.user import User
from app.services.durable_tool_runtime import (
    DurableToolRunService,
    DurableToolRuntimeError,
    DurableToolWorker,
    utcnow,
)
from app.services.tools.executor import ToolExecutor
from app.services.tools.schemas import ExternalSource, PlannedToolCall, ToolCallResult, ToolTraceEvent


class SuccessfulExecutor:
    def __init__(self, **_: object) -> None:
        pass

    async def execute(self, call):
        return (
            ToolCallResult(
                call=call,
                status="success",
                elapsed_ms=3,
                sources=[
                    ExternalSource(
                        source_type="test",
                        provider="test",
                        title=f"{call.call_id} result",
                        display_text="safe structured tool result",
                        metadata={"value": "bound-value"},
                    )
                ],
            ),
            [ToolTraceEvent(type="tool_call_end", payload={"call_id": call.call_id, "status": "success"})],
        )


class FailingExecutor:
    def __init__(self, **_: object) -> None:
        pass

    async def execute(self, call):
        return ToolCallResult(call=call, status="error", elapsed_ms=1, sources=[], error_message="temporary outage"), []


class PermanentFailureExecutor:
    def __init__(self, **_: object) -> None:
        pass

    async def execute(self, call):
        return ToolCallResult(
            call=call,
            status="error",
            elapsed_ms=1,
            sources=[],
            error_message="resource does not exist",
            retryable=False,
        ), []


class SelectiveExecutor(SuccessfulExecutor):
    async def execute(self, call):
        if call.call_id == "fails":
            return ToolCallResult(
                call=call,
                status="error",
                elapsed_ms=1,
                sources=[],
                error_message="temporary outage",
            ), []
        return await super().execute(call)


class BindingExecutor(SuccessfulExecutor):
    async def execute(self, call):
        if call.call_id == "source":
            return (
                ToolCallResult(
                    call=call,
                    status="success",
                    elapsed_ms=1,
                    sources=[
                        ExternalSource(
                            source_type="test",
                            provider="test",
                            title="binding source",
                            display_text="untrusted display text",
                            metadata={"raw": {"query": "bound-value"}},
                        )
                    ],
                ),
                [],
            )
        if call.arguments.get("query") != "bound-value":
            raise AssertionError("durable result binding did not reach the downstream call")
        return await super().execute(call)


class DurableToolRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.SessionLocal() as db:
            user = User(username="durable-user", email="durable@example.test")
            db.add(user)
            db.flush()
            project = Project(user_id=user.id, name="durable-project")
            db.add(project)
            db.flush()
            conversation = Conversation(
                user_id=user.id,
                project_id=project.id,
                title="durable-conversation",
                model_name="test-model",
            )
            db.add(conversation)
            db.flush()
            assistant_message = Message(
                conversation_id=conversation.id,
                sequence=1,
                role="assistant",
                content="",
                status="streaming",
            )
            db.add(assistant_message)
            db.commit()
            self.user_id = user.id
            self.project_id = project.id
            self.conversation_id = conversation.id
            self.assistant_message_id = assistant_message.id

    def tearDown(self) -> None:
        self.engine.dispose()

    def _enqueue(self, calls, *, attempts: int = 3):
        with self.SessionLocal() as db:
            return DurableToolRunService(db).enqueue(
                user_id=self.user_id,
                project_id=self.project_id,
                conversation_id=None,
                assistant_message_id=None,
                calls=calls,
                max_attempts=attempts,
            ).id

    def test_enqueue_is_idempotent_and_worker_persists_artifact(self) -> None:
        calls = [
            {"call_id": "list", "tool_key": "workspace.files.list", "arguments": {}},
            {"call_id": "again", "tool_key": "workspace.files.list", "arguments": {}, "depends_on": ["list"]},
        ]
        run_id = self._enqueue(calls)
        self.assertEqual(self._enqueue(calls), run_id)

        worker = DurableToolWorker(session_factory=self.SessionLocal, owner="worker-a", executor_factory=SuccessfulExecutor)
        self.assertTrue(asyncio.run(worker.run_once()))
        self.assertTrue(asyncio.run(worker.run_once()))
        self.assertFalse(asyncio.run(worker.run_once()))

        with self.SessionLocal() as db:
            run = db.get(AgentRun, run_id)
            self.assertEqual(run.runtime_kind, "durable_tool_workflow")
            self.assertEqual(run.status, "succeeded")
            self.assertEqual(db.query(AgentArtifact).filter_by(run_id=run_id).count(), 2)
            self.assertEqual([step.status for step in db.scalars(select(AgentStep).where(AgentStep.run_id == run_id).order_by(AgentStep.sequence))], ["succeeded", "succeeded"])
            self.assertEqual({event.status for event in db.scalars(select(AgentOutboxEvent).where(AgentOutboxEvent.run_id == run_id))}, {"succeeded"})

            artifact = db.scalar(select(AgentArtifact).where(AgentArtifact.run_id == run_id).limit(1))
            result, _ = asyncio.run(
                ToolExecutor(db=db, user_id=self.user_id, project_id=self.project_id).execute(
                    PlannedToolCall(
                        call_id="read-artifact",
                        tool_key="agent.artifacts.read",
                        provider="agent_runtime",
                        category="agent_artifact",
                        display_name="Agent 运行产物按需读取",
                        confidence=1.0,
                        reason="test bounded artifact read",
                        arguments={"artifact_id": artifact.id, "max_chars": 600},
                    )
                )
            )
            self.assertEqual(result.status, "success")
            self.assertIn("safe structured tool result", result.sources[0].display_text)

    def test_cyclic_dag_is_rejected_before_writing_state(self) -> None:
        with self.SessionLocal() as db:
            with self.assertRaisesRegex(DurableToolRuntimeError, "循环依赖"):
                DurableToolRunService(db).enqueue(
                    user_id=self.user_id,
                    project_id=self.project_id,
                    conversation_id=None,
                    assistant_message_id=None,
                    calls=[
                        {"call_id": "a", "tool_key": "workspace.files.list", "arguments": {}, "depends_on": ["b"]},
                        {"call_id": "b", "tool_key": "workspace.files.list", "arguments": {}, "depends_on": ["a"]},
                    ],
                )
            self.assertEqual(db.query(AgentRun).count(), 0)

    def test_durable_binding_reuses_bounded_metadata_raw_policy(self) -> None:
        with self.SessionLocal() as db:
            with self.assertRaisesRegex(DurableToolRuntimeError, "metadata.raw"):
                DurableToolRunService(db).enqueue(
                    user_id=self.user_id,
                    project_id=self.project_id,
                    conversation_id=None,
                    assistant_message_id=None,
                    calls=[
                        {"call_id": "source", "tool_key": "workspace.files.list", "arguments": {}},
                        {
                            "call_id": "target",
                            "tool_key": "workspace.files.search",
                            "arguments": {},
                            "depends_on": ["source"],
                            "result_bindings": [
                                {
                                    "source_call_id": "source",
                                    "source_path": "/sources/0/display_text",
                                    "target_argument": "query",
                                }
                            ],
                        },
                    ],
                )
            self.assertEqual(db.query(AgentRun).count(), 0)

    def test_durable_binding_resolves_metadata_raw_into_downstream_arguments(self) -> None:
        run_id = self._enqueue(
            [
                {"call_id": "source", "tool_key": "workspace.files.list", "arguments": {}},
                {
                    "call_id": "target",
                    "tool_key": "workspace.files.search",
                    "arguments": {},
                    "depends_on": ["source"],
                    "result_bindings": [
                        {
                            "source_call_id": "source",
                            "source_path": "/sources/0/metadata/raw/query",
                            "target_argument": "query",
                        }
                    ],
                },
            ]
        )
        worker = DurableToolWorker(
            session_factory=self.SessionLocal,
            owner="worker-binding",
            executor_factory=BindingExecutor,
        )
        self.assertTrue(asyncio.run(worker.run_once()))
        self.assertTrue(asyncio.run(worker.run_once()))
        with self.SessionLocal() as db:
            self.assertEqual(db.get(AgentRun, run_id).status, "succeeded")

    def test_explicit_idempotency_key_rejects_a_different_request(self) -> None:
        with self.SessionLocal() as db:
            service = DurableToolRunService(db)
            first = service.enqueue(
                user_id=self.user_id,
                project_id=self.project_id,
                conversation_id=None,
                assistant_message_id=None,
                idempotency_key="client-request-1",
                calls=[{"call_id": "list", "tool_key": "workspace.files.list", "arguments": {}}],
            )
            same = service.enqueue(
                user_id=self.user_id,
                project_id=self.project_id,
                conversation_id=None,
                assistant_message_id=None,
                idempotency_key="client-request-1",
                calls=[{"call_id": "list", "tool_key": "workspace.files.list", "arguments": {}}],
            )
            self.assertEqual(same.id, first.id)

            with self.assertRaisesRegex(DurableToolRuntimeError, "幂等键"):
                service.enqueue(
                    user_id=self.user_id,
                    project_id=self.project_id,
                    conversation_id=None,
                    assistant_message_id=None,
                    idempotency_key="client-request-1",
                    calls=[{"call_id": "search", "tool_key": "workspace.files.search", "arguments": {"query": "different"}}],
                )

            with self.assertRaisesRegex(DurableToolRuntimeError, "不能为空白"):
                service.enqueue(
                    user_id=self.user_id,
                    project_id=self.project_id,
                    conversation_id=None,
                    assistant_message_id=None,
                    idempotency_key="   ",
                    calls=[{"call_id": "blank-key", "tool_key": "workspace.files.list", "arguments": {}}],
                )

    def test_scope_references_must_belong_to_current_user(self) -> None:
        with self.SessionLocal() as db:
            other = User(username="other-user", email="other@example.test")
            db.add(other)
            db.flush()
            foreign_project = Project(user_id=other.id, name="foreign-project")
            db.add(foreign_project)
            db.commit()

            with self.assertRaisesRegex(DurableToolRuntimeError, "项目不存在"):
                DurableToolRunService(db).enqueue(
                    user_id=self.user_id,
                    project_id=foreign_project.id,
                    conversation_id=None,
                    assistant_message_id=None,
                    calls=[{"tool_key": "workspace.files.list", "arguments": {}}],
                )

            run = DurableToolRunService(db).enqueue(
                user_id=self.user_id,
                project_id=self.project_id,
                conversation_id=self.conversation_id,
                assistant_message_id=self.assistant_message_id,
                calls=[{"tool_key": "workspace.files.list", "arguments": {}}],
            )
            self.assertEqual(run.assistant_message_id, self.assistant_message_id)

    def test_stale_worker_cannot_finish_after_lease_is_reclaimed(self) -> None:
        run_id = self._enqueue(
            [{"call_id": "list", "tool_key": "workspace.files.list", "arguments": {}}]
        )
        with self.SessionLocal() as db:
            first_claim = DurableToolRunService(db).claim_next(owner="worker-old", lease_seconds=15)
            event = db.get(AgentOutboxEvent, first_claim.outbox_event_id)
            event.lease_expires_at = utcnow() - timedelta(seconds=1)
            db.commit()

        with self.SessionLocal() as db:
            new_claim = DurableToolRunService(db).claim_next(owner="worker-new", lease_seconds=15)
        self.assertGreater(new_claim.outbox_lease_version, first_claim.outbox_lease_version)

        old_worker = DurableToolWorker(
            session_factory=self.SessionLocal,
            owner="worker-old",
            executor_factory=SuccessfulExecutor,
        )
        with self.SessionLocal() as db:
            asyncio.run(old_worker._execute_claim(db, first_claim))

        with self.SessionLocal() as db:
            step = db.scalar(select(AgentStep).where(AgentStep.run_id == run_id))
            self.assertEqual(step.status, "running")
            self.assertEqual(step.lease_owner, "worker-new")
            self.assertEqual(db.query(AgentArtifact).filter_by(run_id=run_id).count(), 0)

    def test_active_worker_can_renew_its_lease(self) -> None:
        self._enqueue([{"call_id": "list", "tool_key": "workspace.files.list", "arguments": {}}])
        with self.SessionLocal() as db:
            claim = DurableToolRunService(db).claim_next(owner="worker-heartbeat", lease_seconds=15)
            event = db.get(AgentOutboxEvent, claim.outbox_event_id)
            original_expiry = event.lease_expires_at
            self.assertTrue(
                DurableToolRunService(db).renew_claim(
                    claim=claim,
                    owner="worker-heartbeat",
                    lease_seconds=60,
                )
            )
            db.refresh(event)
            step = db.get(AgentStep, claim.step_id)
            self.assertGreater(event.lease_expires_at, original_expiry)
            self.assertEqual(step.lease_expires_at, event.lease_expires_at)
            self.assertIsNotNone(step.heartbeat_at)

    def test_dependency_poll_does_not_create_checkpoint_churn_or_hide_running_work(self) -> None:
        run_id = self._enqueue(
            [
                {"call_id": "source", "tool_key": "workspace.files.list", "arguments": {}},
                {
                    "call_id": "dependent",
                    "tool_key": "workspace.files.list",
                    "arguments": {},
                    "depends_on": ["source"],
                },
            ]
        )
        with self.SessionLocal() as db:
            source_claim = DurableToolRunService(db).claim_next(owner="worker-source")
            dependent_claim = DurableToolRunService(db).claim_next(owner="worker-dependent")
            self.assertNotEqual(source_claim.step_id, dependent_claim.step_id)
        worker = DurableToolWorker(
            session_factory=self.SessionLocal,
            owner="worker-dependent",
            executor_factory=SuccessfulExecutor,
        )
        with self.SessionLocal() as db:
            asyncio.run(worker._execute_claim(db, dependent_claim))
        with self.SessionLocal() as db:
            run = db.get(AgentRun, run_id)
            dependent = db.get(AgentStep, dependent_claim.step_id)
            self.assertEqual(run.status, "running")
            self.assertEqual(run.state_version, 1)
            self.assertEqual(dependent.status, "pending")
            self.assertEqual(
                db.query(AgentCheckpoint).filter_by(run_id=run_id).count(),
                1,
            )

    def test_stale_in_memory_claim_is_fenced_before_writing_artifact(self) -> None:
        run_id = self._enqueue(
            [{"call_id": "list", "tool_key": "workspace.files.list", "arguments": {}}]
        )
        old_db = self.SessionLocal()
        try:
            claim = DurableToolRunService(old_db).claim_next(owner="worker-old", lease_seconds=15)
            stale_event = old_db.get(AgentOutboxEvent, claim.outbox_event_id)
            stale_step = old_db.get(AgentStep, claim.step_id)
            stale_run = old_db.get(AgentRun, run_id)

            with self.SessionLocal() as db:
                current_event = db.get(AgentOutboxEvent, claim.outbox_event_id)
                current_event.lease_expires_at = utcnow() - timedelta(seconds=1)
                db.commit()
            with self.SessionLocal() as db:
                reclaimed = DurableToolRunService(db).claim_next(
                    owner="worker-new",
                    lease_seconds=15,
                )
                self.assertGreater(reclaimed.outbox_lease_version, claim.outbox_lease_version)

            DurableToolWorker(
                session_factory=self.SessionLocal,
                owner="worker-old",
                executor_factory=SuccessfulExecutor,
            )._finish_success(
                old_db,
                stale_run,
                stale_step,
                stale_event,
                claim,
                {"call_id": "list", "tool_key": "workspace.files.list", "sources": [], "events": [], "elapsed_ms": 1},
            )
        finally:
            old_db.close()

        with self.SessionLocal() as db:
            step = db.scalar(select(AgentStep).where(AgentStep.run_id == run_id))
            self.assertEqual(step.status, "running")
            self.assertEqual(step.lease_owner, "worker-new")
            self.assertEqual(db.query(AgentArtifact).filter_by(run_id=run_id).count(), 0)

    def test_failed_read_only_tool_retries_then_reaches_dlq(self) -> None:
        run_id = self._enqueue(
            [{"call_id": "search", "tool_key": "workspace.files.search", "arguments": {"query": "test"}}],
            attempts=2,
        )
        worker = DurableToolWorker(session_factory=self.SessionLocal, owner="worker-b", executor_factory=FailingExecutor)
        self.assertTrue(asyncio.run(worker.run_once()))
        with self.SessionLocal() as db:
            step = db.scalar(select(AgentStep).where(AgentStep.run_id == run_id))
            event = db.scalar(select(AgentOutboxEvent).where(AgentOutboxEvent.run_id == run_id))
            self.assertEqual(step.status, "pending")
            self.assertEqual(step.attempts, 1)
            # Do not wait in the unit test; make the scheduled retry claimable.
            step.available_at = utcnow() - timedelta(seconds=1)
            event.available_at = utcnow() - timedelta(seconds=1)
            db.commit()
        self.assertTrue(asyncio.run(worker.run_once()))
        with self.SessionLocal() as db:
            run = db.get(AgentRun, run_id)
            step = db.scalar(select(AgentStep).where(AgentStep.run_id == run_id))
            event = db.scalar(select(AgentOutboxEvent).where(AgentOutboxEvent.run_id == run_id))
            self.assertEqual(step.attempts, 2)
            self.assertEqual(step.status, "dead_letter")
            self.assertEqual(event.status, "dead_letter")
            self.assertEqual(run.status, "dead_letter")

    def test_permanent_tool_error_fails_without_retrying(self) -> None:
        run_id = self._enqueue(
            [{"call_id": "read", "tool_key": "workspace.files.read", "arguments": {"file_id": "missing"}}],
            attempts=3,
        )
        worker = DurableToolWorker(
            session_factory=self.SessionLocal,
            owner="worker-permanent",
            executor_factory=PermanentFailureExecutor,
        )
        self.assertTrue(asyncio.run(worker.run_once()))
        self.assertFalse(asyncio.run(worker.run_once()))
        with self.SessionLocal() as db:
            run = db.get(AgentRun, run_id)
            step = db.scalar(select(AgentStep).where(AgentStep.run_id == run_id))
            event = db.scalar(select(AgentOutboxEvent).where(AgentOutboxEvent.run_id == run_id))
            self.assertEqual(step.attempts, 1)
            self.assertEqual(step.status, "failed")
            self.assertEqual(step.error_code, "permanent_tool_error")
            self.assertEqual(event.status, "failed")
            self.assertEqual(run.status, "failed")

    def test_dead_letter_allows_independent_branches_to_finish(self) -> None:
        run_id = self._enqueue(
            [
                {"call_id": "fails", "tool_key": "workspace.files.list", "arguments": {}},
                {
                    "call_id": "depends",
                    "tool_key": "workspace.files.list",
                    "arguments": {},
                    "depends_on": ["fails"],
                },
                {"call_id": "independent", "tool_key": "workspace.files.list", "arguments": {}},
            ],
            attempts=1,
        )
        worker = DurableToolWorker(
            session_factory=self.SessionLocal,
            owner="worker-branches",
            executor_factory=SelectiveExecutor,
        )
        self.assertTrue(asyncio.run(worker.run_once()))
        self.assertTrue(asyncio.run(worker.run_once()))
        self.assertTrue(asyncio.run(worker.run_once()))
        self.assertFalse(asyncio.run(worker.run_once()))

        with self.SessionLocal() as db:
            run = db.get(AgentRun, run_id)
            statuses = {
                step.call_id: step.status
                for step in db.scalars(select(AgentStep).where(AgentStep.run_id == run_id))
            }
            self.assertEqual(
                statuses,
                {"fails": "dead_letter", "depends": "skipped", "independent": "succeeded"},
            )
            self.assertEqual(run.status, "dead_letter")
            self.assertIsNotNone(run.finished_at)

    def test_artifact_tool_requires_an_explicit_project_scope(self) -> None:
        run_id = self._enqueue(
            [{"call_id": "list", "tool_key": "workspace.files.list", "arguments": {}}]
        )
        worker = DurableToolWorker(
            session_factory=self.SessionLocal,
            owner="worker-artifact",
            executor_factory=SuccessfulExecutor,
        )
        self.assertTrue(asyncio.run(worker.run_once()))
        with self.SessionLocal() as db:
            artifact = db.scalar(select(AgentArtifact).where(AgentArtifact.run_id == run_id))
            result, _ = asyncio.run(
                ToolExecutor(db=db, user_id=self.user_id, project_id=None).execute(
                    PlannedToolCall(
                        call_id="unscoped-artifact-read",
                        tool_key="agent.artifacts.read",
                        provider="agent_runtime",
                        category="agent_artifact",
                        display_name="Agent 运行产物按需读取",
                        confidence=1.0,
                        reason="verify fail-closed project scope",
                        arguments={"artifact_id": artifact.id},
                    )
                )
            )
            self.assertEqual(result.status, "error")
            self.assertIn("明确的工作区", result.error_message)


if __name__ == "__main__":
    unittest.main()
