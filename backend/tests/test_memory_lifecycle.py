from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.user import User
from app.models.user_memory import UserMemory
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.memory_repo import UserMemoryRepository
from app.schemas.memory import UserMemoryCreate, UserMemoryUpdate
from app.services.memory_service import MemoryService


class MemoryLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.SessionLocal = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.SessionLocal() as db:
            user = User(username="memory-lifecycle", email="memory-lifecycle@example.test")
            db.add(user)
            db.commit()
            self.user_id = user.id

    def tearDown(self) -> None:
        self.engine.dispose()

    def _service(self, db):  # noqa: ANN001
        return MemoryService(UserMemoryRepository(db), ConversationRepository(db))

    def test_expired_memory_is_closed_and_not_injected(self) -> None:
        with self.SessionLocal() as db:
            memory = UserMemory(
                user_id=self.user_id,
                memory_type="fact",
                title="temporary",
                content="temporary fact",
                status="active",
                is_enabled=True,
                expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
            )
            db.add(memory)
            db.commit()

            context, count, _ = self._service(db).build_memory_context(
                self.user_id,
                max_chars=1000,
                query="temporary fact",
            )

            db.refresh(memory)
            self.assertIsNone(context)
            self.assertEqual(count, 0)
            self.assertEqual(memory.status, "expired")
            self.assertFalse(memory.is_enabled)

    def test_revoke_is_explicit_and_cannot_be_reactivated_by_generic_update(self) -> None:
        with self.SessionLocal() as db:
            service = self._service(db)
            response = service.create_memory(
                self.user_id,
                UserMemoryCreate(title="preference", content="use concise answers"),
            )
            memory = UserMemoryRepository(db).get_by_user(response.id, self.user_id)
            assert memory is not None

            revoked = service.revoke_memory(memory=memory)
            self.assertEqual(revoked.status, "revoked")
            self.assertFalse(revoked.is_enabled)
            with self.assertRaisesRegex(ValueError, "非 active"):
                service.update_memory(memory=memory, payload=UserMemoryUpdate(is_enabled=True))
            self.assertEqual(UserMemoryRepository(db).list_by_user(self.user_id, enabled_only=True), [])

    def test_conflict_approval_supersedes_previous_active_version(self) -> None:
        with self.SessionLocal() as db:
            repo = UserMemoryRepository(db)
            old = UserMemory(
                user_id=self.user_id,
                memory_type="profile",
                title="回答语言",
                content="中文",
                status="active",
                is_enabled=True,
            )
            db.add(old)
            db.commit()
            candidate = UserMemory(
                user_id=self.user_id,
                memory_type="profile",
                title="回答语言",
                content="英文",
                status="pending",
                is_enabled=False,
                risk_level="conflict",
                supersedes_memory_id=old.id,
            )
            db.add(candidate)
            db.commit()

            approved = self._service(db).approve_candidate(memory=candidate)

            db.refresh(old)
            db.refresh(candidate)
            self.assertEqual(approved.status, "active")
            self.assertEqual(old.status, "superseded")
            self.assertFalse(old.is_enabled)
            self.assertEqual(candidate.status, "active")
            self.assertTrue(candidate.is_enabled)

    def test_conflict_candidate_without_version_target_is_rejected(self) -> None:
        with self.SessionLocal() as db:
            candidate = UserMemory(
                user_id=self.user_id,
                memory_type="fact",
                title="conflict",
                content="new value",
                status="pending",
                is_enabled=False,
                risk_level="conflict",
            )
            db.add(candidate)
            db.commit()

            with self.assertRaisesRegex(ValueError, "supersedes_memory_id"):
                self._service(db).approve_candidate(memory=candidate)


if __name__ == "__main__":
    unittest.main()
