from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User
from app.models.user_memory import MemoryExtractionJob, UserMemory
from app.services.memory_candidate_runtime import MemoryCandidateWorker, MemoryExtractionJobService


class MemoryCandidateRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.SessionLocal() as db:
            user = User(username="memory-user", email="memory@example.test")
            db.add(user)
            db.flush()
            conversation = Conversation(user_id=user.id, title="memory", model_name="model-a")
            db.add(conversation)
            db.flush()
            db.add_all(
                [
                    Message(conversation_id=conversation.id, sequence=1, role="user", content="以后回答都使用中文。"),
                    Message(conversation_id=conversation.id, sequence=2, role="assistant", content="好的。", status="done"),
                ]
            )
            db.commit()
            self.user_id = user.id
            self.conversation_id = conversation.id

    def tearDown(self) -> None:
        self.engine.dispose()

    def test_worker_only_creates_pending_candidate_and_is_idempotent(self) -> None:
        with self.SessionLocal() as db:
            assistant = db.query(Message).filter_by(role="assistant").one()
            job = MemoryExtractionJobService(db).enqueue_after_turn(
                user_id=self.user_id,
                conversation_id=self.conversation_id,
                assistant_message_id=assistant.id,
                force=True,
            )
            self.assertIsNotNone(job)
            duplicate = MemoryExtractionJobService(db).enqueue_after_turn(
                user_id=self.user_id,
                conversation_id=self.conversation_id,
                assistant_message_id=assistant.id,
                force=True,
            )
            self.assertEqual(duplicate.id, job.id)

        provider = type("Provider", (), {})()
        provider.complete_chat = AsyncMock(
            return_value='[{"memory_type":"instruction","title":"回答语言","content":"以后回答使用中文",'
            '"reason":"用户明确要求","confidence":"high"}]'
        )
        worker = MemoryCandidateWorker(
            session_factory=self.SessionLocal,
            owner="test-worker",
            provider_service=provider,
        )
        self.assertTrue(asyncio.run(worker.run_once()))

        with self.SessionLocal() as db:
            stored_job = db.query(MemoryExtractionJob).one()
            memory = db.query(UserMemory).one()
            self.assertEqual(stored_job.status, "succeeded")
            self.assertEqual(stored_job.result_count, 1)
            self.assertEqual(memory.status, "pending")
            self.assertFalse(memory.is_enabled)
            self.assertEqual(memory.risk_level, "review_required")


if __name__ == "__main__":
    unittest.main()
