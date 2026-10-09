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
from app.models.project import Project
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

    def test_project_fact_is_scoped_and_credential_output_is_not_persisted(self) -> None:
        with self.SessionLocal() as db:
            project = Project(user_id=self.user_id, name="候选范围测试")
            db.add(project)
            db.flush()
            db.get(Conversation, self.conversation_id).project_id = project.id
            db.commit()
            project_id = project.id
            assistant = db.query(Message).filter_by(role="assistant").one()
            MemoryExtractionJobService(db).enqueue_after_turn(user_id=self.user_id,
                conversation_id=self.conversation_id, assistant_message_id=assistant.id, force=True)
        provider = type("Provider", (), {})()
        provider.complete_chat = AsyncMock(return_value='[{"memory_type":"fact","title":"数据库",'
            '"content":"项目使用 PostgreSQL","confidence":"high"},'
            '{"memory_type":"fact","title":"凭证","content":"password=fake-only-test"}]')
        self.assertTrue(asyncio.run(MemoryCandidateWorker(session_factory=self.SessionLocal,
            owner="scope-test-worker", provider_service=provider).run_once()))
        with self.SessionLocal() as db:
            memory = db.query(UserMemory).one()
            self.assertEqual(memory.project_id, project_id)
            self.assertEqual(memory.status, "pending")
            self.assertIsNotNone(memory.fact_key)
            self.assertEqual(db.query(MemoryExtractionJob).one().result_count, 1)

    def test_old_duplicate_label_does_not_hide_language_value_change(self) -> None:
        with self.SessionLocal() as db:
            old = UserMemory(user_id=self.user_id, memory_type="profile", title="默认回答语言",
                             content="用户默认喜欢中文回答", status="active", is_enabled=True)
            db.add(old)
            db.commit()
            old_id = old.id
            assistant = db.query(Message).filter_by(role="assistant").one()
            MemoryExtractionJobService(db).enqueue_after_turn(user_id=self.user_id,
                conversation_id=self.conversation_id, assistant_message_id=assistant.id, force=True)
        provider = type("Provider", (), {})()
        provider.complete_chat = AsyncMock(return_value='[{"memory_type":"profile","title":"默认回答语言",'
            '"content":"用户默认喜欢英文回答","confidence":"high"}]')
        self.assertTrue(asyncio.run(MemoryCandidateWorker(session_factory=self.SessionLocal,
            owner="conflict-test-worker", provider_service=provider).run_once()))
        with self.SessionLocal() as db:
            candidate = db.query(UserMemory).filter_by(status="pending").one()
            self.assertEqual(candidate.risk_level, "conflict")
            self.assertEqual(candidate.supersedes_memory_id, old_id)
            self.assertEqual(candidate.fact_value, "en")


if __name__ == "__main__":
    unittest.main()
