from __future__ import annotations

import json
import unittest
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.core.config import settings
from app.models import *  # noqa: F403 - register the complete FK graph.
from app.models.knowledge import (
    KnowledgeBase,
    KnowledgeDocument,
    KnowledgeIndexGeneration,
    KnowledgeJob,
    OutboxEvent,
)
from app.models.user import User
from app.repositories.knowledge_repo import (
    KnowledgeBaseRepository,
    KnowledgeChunkRepository,
    KnowledgeDocumentRepository,
    KnowledgeJobRepository,
)
from app.schemas.knowledge import KnowledgeDocumentCreate
from app.services.knowledge_index_service import IndexResult, KnowledgeIndexService
from app.services.knowledge_job_runtime import (
    JobLease,
    KnowledgeJobCoordinator,
    KnowledgeJobWorker,
    KnowledgeOutboxPublisher,
    utcnow,
)
from app.services.knowledge_parser_service import ParseResult
from app.services.knowledge_service import KnowledgeDocumentService


class FakeRedis:
    def __init__(self) -> None:
        self.added: list[tuple[str, dict[str, str]]] = []
        self.acked: list[tuple[str, str, str]] = []

    def xadd(self, stream: str, fields: dict[str, str]):
        self.added.append((stream, fields))
        return f"{len(self.added)}-0"

    def xack(self, stream: str, group: str, message_id: str):
        self.acked.append((stream, group, str(message_id)))
        return 1


class KnowledgeJobRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        self.SessionLocal = sessionmaker(bind=self.engine, autocommit=False, autoflush=False, expire_on_commit=False)
        Base.metadata.create_all(bind=self.engine)
        self.db = self.SessionLocal()
        self.user = User(email=f"runtime-{uuid4()}@example.com", username=f"runtime-{uuid4()}")
        self.db.add(self.user)
        self.db.flush()
        self.knowledge_base = KnowledgeBase(
            user_id=self.user.id,
            name="Runtime",
            embedding_provider="openai-compatible",
            embedding_model="fake",
            embedding_dimensions=128,
        )
        self.db.add(self.knowledge_base)
        self.db.flush()
        self.document = KnowledgeDocument(
            knowledge_base_id=self.knowledge_base.id,
            user_id=self.user.id,
            file_name="runtime.md",
            storage_key=f"{self.user.id}/runtime.md",
            parse_status="pending",
            index_status="pending",
        )
        self.db.add(self.document)
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        Base.metadata.drop_all(bind=self.engine)
        self.engine.dispose()

    def _job(self, job_type: str = "parse_document", *, max_attempts: int = 3) -> KnowledgeJob:
        job = KnowledgeJob(
            user_id=self.user.id,
            knowledge_base_id=self.knowledge_base.id,
            document_id=self.document.id,
            job_type=job_type,
            status="pending",
            retry_count=0,
            max_attempts=max_attempts,
            available_at=utcnow(),
            idempotency_key=f"{job_type}:{uuid4()}",
        )
        self.db.add(job)
        self.db.commit()
        return job

    def test_enqueue_parse_persists_job_and_outbox_together(self) -> None:
        service = KnowledgeDocumentService(
            KnowledgeDocumentRepository(self.db),
            KnowledgeBaseRepository(self.db),
            KnowledgeJobRepository(self.db),
        )
        initial = service.add_document(
            self.knowledge_base.id,
            self.user.id,
            KnowledgeDocumentCreate(
                file_name="queued.md",
                storage_key=f"{self.user.id}/queued.md",
            ),
        )
        assert initial is not None

        response = service.enqueue_parse_document(self.knowledge_base.id, initial.id, self.user.id)

        self.assertIsNotNone(response)
        assert response is not None
        event = self.db.query(OutboxEvent).filter_by(aggregate_id=response.job.id).one()
        self.assertEqual(response.job.status, "pending")
        self.assertEqual(response.document.parse_status, "queued")
        self.assertEqual(json.loads(event.payload_json)["job_id"], response.job.id)

    def test_lease_expiry_allows_takeover_and_fences_old_worker(self) -> None:
        job = self._job()
        coordinator = KnowledgeJobCoordinator(session_factory=self.SessionLocal, lease_seconds=30)
        first = coordinator.claim(job.id, "worker-a")
        self.assertEqual(first.state, "claimed")
        second = coordinator.claim(job.id, "worker-b")
        self.assertEqual(second.state, "busy")
        with self.SessionLocal() as db:
            stored = db.get(KnowledgeJob, job.id)
            stored.lease_expires_at = utcnow() - timedelta(seconds=1)
            db.commit()

        takeover = coordinator.claim(job.id, "worker-b")

        self.assertEqual(takeover.state, "claimed")
        assert first.lease is not None and takeover.lease is not None
        self.assertEqual(takeover.lease.version, first.lease.version + 1)
        stale_result = coordinator.complete_parse(
            first.lease,
            ParseResult(markdown_path="stale.md", markdown_preview="stale"),
        )
        fresh_result = coordinator.complete_parse(
            takeover.lease,
            ParseResult(markdown_path="fresh.md", markdown_preview="fresh"),
        )
        self.assertFalse(stale_result)
        self.assertTrue(fresh_result)
        with self.SessionLocal() as db:
            self.assertEqual(db.get(KnowledgeDocument, self.document.id).parsed_markdown_path, "fresh.md")

    def test_transient_failure_requeues_with_new_outbox_event(self) -> None:
        job = self._job(max_attempts=3)
        coordinator = KnowledgeJobCoordinator(session_factory=self.SessionLocal)
        claim = coordinator.claim(job.id, "worker-a")
        assert claim.lease is not None

        transitioned = coordinator.fail(claim.lease, TimeoutError("provider timeout"))

        self.assertTrue(transitioned)
        with self.SessionLocal() as db:
            stored = db.get(KnowledgeJob, job.id)
            self.assertEqual(stored.status, "pending")
            self.assertEqual(stored.error_code, "provider_unavailable")
            self.assertIsNotNone(stored.available_at)
            self.assertEqual(db.query(OutboxEvent).filter_by(aggregate_id=job.id).count(), 1)

    def test_permanent_failure_goes_directly_to_dead_letter(self) -> None:
        job = self._job(max_attempts=3)
        coordinator = KnowledgeJobCoordinator(session_factory=self.SessionLocal)
        claim = coordinator.claim(job.id, "worker-a")
        assert claim.lease is not None

        transitioned = coordinator.fail(claim.lease, ValueError("bad input"))

        self.assertTrue(transitioned)
        with self.SessionLocal() as db:
            stored = db.get(KnowledgeJob, job.id)
            document = db.get(KnowledgeDocument, self.document.id)
            self.assertEqual(stored.status, "dead_letter")
            self.assertEqual(stored.error_code, "invalid_request")
            self.assertEqual(document.parse_status, "failed")
            self.assertEqual(db.query(OutboxEvent).filter_by(aggregate_id=job.id).count(), 0)

    def test_outbox_republish_uses_stable_event_id(self) -> None:
        job = self._job()
        event = OutboxEvent(
            event_key=f"knowledge-job:{job.id}",
            aggregate_id=job.id,
            payload_json=json.dumps({"job_id": job.id}),
            status="pending",
            available_at=utcnow(),
        )
        self.db.add(event)
        self.db.commit()
        fake_redis = FakeRedis()
        publisher = KnowledgeOutboxPublisher(fake_redis, session_factory=self.SessionLocal, publisher_id="publisher-a")
        self.assertTrue(publisher.publish_once())
        with self.SessionLocal() as db:
            stored = db.get(OutboxEvent, event.id)
            stored.status = "publishing"
            stored.lease_owner = "dead-publisher"
            stored.lease_expires_at = utcnow() - timedelta(seconds=1)
            db.commit()

        self.assertTrue(publisher.publish_once())

        self.assertEqual(len(fake_redis.added), 2)
        self.assertEqual(fake_redis.added[0][1]["event_id"], fake_redis.added[1][1]["event_id"])

    def test_active_generation_switch_is_guarded_by_base_pointer(self) -> None:
        job = self._job("index_document")
        coordinator = KnowledgeJobCoordinator(session_factory=self.SessionLocal)
        claim = coordinator.claim(job.id, "worker-a")
        assert claim.lease is not None
        generation = KnowledgeIndexGeneration(
            id=f"{job.id}-v1",
            user_id=self.user.id,
            knowledge_base_id=self.knowledge_base.id,
            base_generation_id="legacy",
            job_id=job.id,
            status="ready",
        )
        self.db.add(generation)
        self.db.commit()

        activated = coordinator.activate_generation(
            claim.lease,
            generation.id,
            IndexResult(chunk_count=2, index_path="index.json", manifest_hash="abc"),
        )

        self.assertTrue(activated)
        with self.SessionLocal() as db:
            self.assertEqual(db.get(KnowledgeBase, self.knowledge_base.id).active_index_generation, generation.id)
            self.assertEqual(db.get(KnowledgeJob, job.id).status, "succeeded")

    def test_index_build_keeps_complete_generation_inactive_until_cas(self) -> None:
        class FakeEmbeddingService:
            async def embed_texts(self, *, user_id, knowledge_base, texts):  # noqa: ANN001, ARG002
                return [[1.0, 0.0, 0.0, 0.0] for _ in texts]

        previous_upload_dir = settings.upload_dir
        previous_index_dir = settings.knowledge_index_dir
        with TemporaryDirectory() as upload_dir, TemporaryDirectory() as index_dir:
            object.__setattr__(settings, "upload_dir", upload_dir)
            object.__setattr__(settings, "knowledge_index_dir", index_dir)
            try:
                markdown_dir = Path(upload_dir) / self.user.id / "knowledge" / self.knowledge_base.id
                markdown_dir.mkdir(parents=True)
                markdown_path = markdown_dir / f"{self.document.id}.md"
                markdown_path.write_text("# Reliable RAG\n\nlease fencing and inactive snapshots", encoding="utf-8")
                self.document.parse_status = "parsed"
                self.document.parsed_markdown_path = (
                    f"{self.user.id}/knowledge/{self.knowledge_base.id}/{self.document.id}.md"
                )
                self.knowledge_base.embedding_dimensions = 4
                self.db.commit()
                service = KnowledgeIndexService(
                    chunk_repo=KnowledgeChunkRepository(self.db),
                    document_repo=KnowledgeDocumentRepository(self.db),
                    setting_service=SimpleNamespace(),
                    embedding_service=FakeEmbeddingService(),
                )

                result = service.build_inactive_generation(
                    user_id=self.user.id,
                    knowledge_base=self.knowledge_base,
                    document=self.document,
                    generation_id="generation-test",
                )

                self.db.refresh(self.knowledge_base)
                chunks = KnowledgeChunkRepository(self.db).list_by_knowledge_base(
                    self.knowledge_base.id,
                    self.user.id,
                    index_generation="generation-test",
                )
                self.assertEqual(self.knowledge_base.active_index_generation, "legacy")
                self.assertGreater(len(chunks), 0)
                self.assertEqual(result.chunk_count, len(chunks))
                self.assertTrue(result.manifest_hash)
                self.assertTrue(Path(result.index_path or "").exists())
            finally:
                object.__setattr__(settings, "upload_dir", previous_upload_dir)
                object.__setattr__(settings, "knowledge_index_dir", previous_index_dir)

    def test_worker_acks_only_after_success_state_is_committed(self) -> None:
        job = self._job()
        fake_redis = FakeRedis()
        worker = KnowledgeJobWorker(fake_redis, session_factory=self.SessionLocal, worker_id="worker-a")
        worker._execute_parse = lambda lease: ParseResult(  # type: ignore[method-assign]
            markdown_path="committed.md",
            markdown_preview="ok",
        )

        result = worker.process_message("1-0", {"job_id": job.id})

        self.assertEqual(result, "succeeded")
        self.assertEqual(len(fake_redis.acked), 1)
        with self.SessionLocal() as db:
            self.assertEqual(db.get(KnowledgeJob, job.id).status, "succeeded")
            self.assertEqual(db.get(KnowledgeDocument, self.document.id).parsed_markdown_path, "committed.md")


if __name__ == "__main__":
    unittest.main()
