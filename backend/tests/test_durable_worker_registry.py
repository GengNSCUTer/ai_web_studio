from __future__ import annotations

import asyncio
import unittest
from datetime import timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.agent_runtime import AgentOutboxEvent, AgentRun, AgentStep, AgentWorkerHeartbeat
from app.models.user import User
from app.services.durable_tool_runtime import DurableToolWorker
from app.services.durable_worker_registry import DurableWorkerRegistry, _now


class DurableWorkerRegistryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.SessionLocal() as db:
            user = User(username="worker-test", email="worker-test@example.test")
            db.add(user)
            db.commit()
            self.user_id = user.id

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_online_duplicate_rejected_and_stale_takeover_fenced(self) -> None:
        with self.SessionLocal() as db:
            registry = DurableWorkerRegistry(db)
            registry.check_database()
            first = registry.register(owner="worker-one")
            with self.assertRaisesRegex(RuntimeError, "同名"):
                registry.register(owner="worker-one")
            row = db.get(AgentWorkerHeartbeat, "worker-one")
            row.heartbeat_at = _now() - timedelta(seconds=DurableWorkerRegistry.STALE_SECONDS + 1)
            db.commit()
            second = registry.register(owner="worker-one")
            self.assertNotEqual(first, second)
            self.assertFalse(registry.heartbeat(owner="worker-one", instance_id=first, status="running"))
            registry.stop(owner="worker-one", instance_id=first)
            self.assertTrue(registry.heartbeat(owner="worker-one", instance_id=second, status="running"))
            self.assertEqual(registry.summary(user_id=self.user_id)["busy_workers"], 1)
            registry.stop(owner="worker-one", instance_id=second)
            self.assertEqual(registry.summary(user_id=self.user_id)["online_workers"], 0)

    def test_graceful_stop_records_stopped_status(self) -> None:
        worker = DurableToolWorker(session_factory=self.SessionLocal, owner="worker-graceful")

        async def exercise() -> None:
            task = asyncio.create_task(worker.run_forever(poll_interval_seconds=0.1))
            for _ in range(50):
                with self.SessionLocal() as db:
                    if db.scalar(select(AgentWorkerHeartbeat).where(AgentWorkerHeartbeat.owner == worker.owner)):
                        break
                await asyncio.sleep(0.01)
            await asyncio.sleep(0.25)
            self.assertFalse(task.done(), "空队列轮询超时后 Worker 不应退出")
            worker.request_stop()
            await asyncio.wait_for(task, timeout=3)

        asyncio.run(exercise())
        with self.SessionLocal() as db:
            row = db.get(AgentWorkerHeartbeat, worker.owner)
            self.assertEqual(row.status, "stopped")
            self.assertIsNotNone(row.stopped_at)

    def test_overdue_queue_and_stale_worker_alerts_are_user_scoped(self) -> None:
        with self.SessionLocal() as db:
            other = User(username="other-worker-test", email="other-worker-test@example.test")
            db.add(other)
            db.flush()
            for user_id, suffix in ((self.user_id, "mine"), (other.id, "other")):
                run = AgentRun(
                    user_id=user_id, runtime_kind="durable_tool_workflow", status="queued",
                    idempotency_key=f"worker-health-{suffix}",
                )
                db.add(run)
                db.flush()
                step = AgentStep(
                    run_id=run.id, sequence=1, call_id="list", tool_key="workspace.files.list",
                    arguments_json="{}", arguments_hash="test", status="pending",
                )
                db.add(step)
                db.flush()
                db.add(AgentOutboxEvent(
                    event_key=f"worker-health-{suffix}", run_id=run.id, step_id=step.id,
                    status="pending", available_at=_now() - timedelta(minutes=20),
                    created_at=_now() - timedelta(minutes=20),
                ))
            registry = DurableWorkerRegistry(db)
            instance = registry.register(owner="stale-worker")
            worker_row = db.get(AgentWorkerHeartbeat, "stale-worker")
            worker_row.heartbeat_at = _now() - timedelta(seconds=DurableWorkerRegistry.STALE_SECONDS + 1)
            db.commit()
            summary = registry.summary(user_id=self.user_id)
            self.assertEqual(summary["online_workers"], 0)
            self.assertEqual(summary["pending_events"], 1)
            self.assertEqual(summary["overdue_events"], 1)
            self.assertEqual(summary["stale_workers"], 1)
            self.assertEqual(
                {alert["code"] for alert in summary["alerts"]},
                {"durable_no_online_worker", "durable_worker_stale", "durable_queue_overdue"},
            )
            self.assertFalse(registry.heartbeat(owner="stale-worker", instance_id="other-instance", status="running"))
            registry.stop(owner="stale-worker", instance_id=instance)
