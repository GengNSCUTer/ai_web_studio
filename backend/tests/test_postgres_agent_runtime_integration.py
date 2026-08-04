from __future__ import annotations

import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.database import Base
from app.models import AgentOutboxEvent, User
from app.services.durable_tool_runtime import DurableToolRunService, utcnow


TEST_POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")


@unittest.skipUnless(TEST_POSTGRES_URL, "set TEST_POSTGRES_URL to run PostgreSQL integration tests")
class PostgresAgentRuntimeIntegrationTest(unittest.TestCase):
    """Verify lease/CAS/idempotency behavior on PostgreSQL, not SQLite."""

    @classmethod
    def setUpClass(cls) -> None:
        assert TEST_POSTGRES_URL is not None
        source_url = make_url(TEST_POSTGRES_URL)
        cls.database_name = f"aiws_agent_runtime_test_{uuid4().hex}"
        cls.admin_engine = create_engine(source_url.set(database="postgres"), isolation_level="AUTOCOMMIT")
        cls.database_url = source_url.set(database=cls.database_name)
        with cls.admin_engine.connect() as connection:
            connection.execute(text(f'create database "{cls.database_name}"'))

        cls.engine = create_engine(cls.database_url, pool_pre_ping=True)
        with cls.engine.begin() as connection:
            connection.execute(text("create extension if not exists vector"))
        Base.metadata.create_all(bind=cls.engine)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.engine.dispose()
        with cls.admin_engine.connect() as connection:
            connection.execute(text(f'drop database if exists "{cls.database_name}" with (force)'))
        cls.admin_engine.dispose()

    def setUp(self) -> None:
        with Session(self.engine) as db:
            user = User(email=f"runtime-{uuid4()}@example.com", username=f"runtime-{uuid4()}")
            db.add(user)
            db.commit()
            self.user_id = user.id

    def _enqueue(self, *, idempotency_key: str | None = None) -> str:
        with Session(self.engine) as db:
            run = DurableToolRunService(db).enqueue(
                user_id=self.user_id,
                project_id=None,
                conversation_id=None,
                assistant_message_id=None,
                calls=[
                    {
                        "call_id": "list-files",
                        "tool_key": "workspace.files.list",
                        "arguments": {},
                    }
                ],
                idempotency_key=idempotency_key,
            )
            return run.id

    def test_competing_workers_and_expired_lease_are_fenced(self) -> None:
        run_id = self._enqueue()

        with Session(self.engine) as first_db:
            first_claim = DurableToolRunService(first_db).claim_next(owner="worker-a", lease_seconds=15)
            self.assertIsNotNone(first_claim)

        with Session(self.engine) as second_db:
            self.assertIsNone(DurableToolRunService(second_db).claim_next(owner="worker-b", lease_seconds=15))

        with Session(self.engine) as db:
            event = db.scalar(select(AgentOutboxEvent).where(AgentOutboxEvent.run_id == run_id))
            assert event is not None
            event.lease_expires_at = utcnow() - timedelta(seconds=1)
            db.commit()

        with Session(self.engine) as second_db:
            second_claim = DurableToolRunService(second_db).claim_next(owner="worker-b", lease_seconds=15)
            self.assertIsNotNone(second_claim)
            assert first_claim is not None
            assert second_claim is not None
            self.assertGreater(second_claim.outbox_lease_version, first_claim.outbox_lease_version)
            self.assertFalse(
                DurableToolRunService(second_db).renew_claim(
                    claim=first_claim,
                    owner="worker-a",
                )
            )
            self.assertTrue(
                DurableToolRunService(second_db).renew_claim(
                    claim=second_claim,
                    owner="worker-b",
                )
            )

    def test_concurrent_same_idempotency_key_returns_one_run(self) -> None:
        key = f"same-request-{uuid4()}"

        def enqueue_once() -> str:
            return self._enqueue(idempotency_key=key)

        with ThreadPoolExecutor(max_workers=2) as pool:
            run_ids = list(pool.map(lambda _: enqueue_once(), range(2)))

        self.assertEqual(len(set(run_ids)), 1)
        with Session(self.engine) as db:
            self.assertEqual(
                len(db.scalars(select(AgentOutboxEvent).where(AgentOutboxEvent.run_id == run_ids[0])).all()),
                1,
            )


if __name__ == "__main__":
    unittest.main()
