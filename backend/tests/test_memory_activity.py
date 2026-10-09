"""验证后台记忆通知的真实状态、任务关联与用户/会话隔离。"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.api.deps import get_current_user, get_db
from app.api.routes.memories import router
from app.core.database import Base
from app.models.conversation import Conversation
from app.models.user import User
from app.models.user_memory import MemoryExtractionJob, UserMemory
from app.services.memory_activity_service import MemoryActivityService


class MemoryActivityTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        with self.sessions() as db:
            user = User(username="notice", email="notice@example.test")
            other = User(username="other", email="other@example.test")
            db.add_all([user, other])
            db.flush()
            conv = Conversation(user_id=user.id, title="通知", model_name="test")
            foreign = Conversation(user_id=other.id, title="其他用户", model_name="test")
            db.add_all([conv, foreign])
            db.commit()
            self.user, self.other, self.conv, self.foreign = user.id, other.id, conv.id, foreign.id
        app = FastAPI()
        app.include_router(router, prefix="/api")
        def database():
            with self.sessions() as db:
                yield db
        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=self.user)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.engine.dispose()

    def fixture(self, job_status="succeeded", **memory_values):
        with self.sessions() as db:
            job = MemoryExtractionJob(user_id=self.user, conversation_id=self.conv,
                idempotency_key=uuid4().hex, source_message_ids="test-source", status=job_status)
            db.add(job)
            db.flush()
            values = dict(user_id=self.user, source_conversation_id=self.conv,
                extraction_job_id=job.id, memory_type="fact", title="项目数据库",
                content="项目采用 PostgreSQL 16", status="active", source="auto_confirmed", is_enabled=True)
            values.update(memory_values)
            memory = UserMemory(**values)
            db.add(memory)
            db.commit()
            return memory.id, job.id

    def activity(self):
        with self.sessions() as db:
            return MemoryActivityService(db).get_activity(self.user, self.conv)

    def test_saved_notice_is_deduplicated_and_revocation_changes_current_status(self):
        memory_id, _ = self.fixture()
        first, second = self.activity(), self.activity()
        self.assertEqual(first.model_dump(), second.model_dump())
        self.assertEqual((len(first.items), first.items[0].status), (1, "active"))
        with self.sessions() as db:
            memory = db.get(UserMemory, memory_id)
            memory.status, memory.is_enabled = "revoked", False
            memory.version += 1
            db.commit()
        revoked = self.activity().items[0]
        self.assertEqual(revoked.event_id, first.items[0].event_id)
        self.assertEqual(revoked.status, "revoked")

    def test_pending_candidate_is_never_reported_as_saved(self):
        self.fixture(status="pending", is_enabled=False, source="auto_candidate")
        self.assertEqual(self.activity().items[0].status, "pending")

    def test_running_or_failed_job_never_exposes_saved_notice(self):
        for status in ("running", "failed"):
            self.fixture(job_status=status)
        result = self.activity()
        self.assertTrue(result.has_pending_jobs)
        self.assertEqual(result.items, [])

    def test_legacy_unlinked_memory_is_not_guessed_into_job(self):
        self.fixture(extraction_job_id=None)
        self.assertEqual(self.activity().items, [])

    def test_other_user_or_source_conversation_cannot_leak_via_job_id(self):
        self.fixture(user_id=self.other)
        self.fixture(source_conversation_id=self.foreign)
        self.assertEqual(self.activity().items, [])

    def test_expired_or_disabled_active_row_does_not_claim_saved(self):
        self.fixture(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        self.fixture(is_enabled=False)
        self.assertEqual({item.status for item in self.activity().items}, {"expired", "disabled"})

    def test_sensitive_content_and_credentials_are_not_exposed(self):
        self.fixture(content="api_key: do-not-expose")
        self.fixture(content="个人隐私", sensitivity="sensitive", status="pending")
        result = self.activity()
        self.assertNotIn("do-not-expose", result.model_dump_json())
        self.assertNotIn("个人隐私", result.model_dump_json())

    def test_endpoint_enforces_ownership_and_no_store(self):
        self.fixture()
        response = self.client.get(f"/api/memories/activity/{self.conv}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(len(response.json()["items"]), 1)
        self.assertEqual(self.client.get(f"/api/memories/activity/{self.foreign}").status_code, 404)
        self.assertEqual(self.client.get(f"/api/memories/activity/{uuid4()}").status_code, 404)
