import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from redis import Redis
from sqlalchemy import create_engine, make_url, text
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.migrations import upgrade_database
from app.models.knowledge import KnowledgeBase, KnowledgeDocument, KnowledgeIndexGeneration, KnowledgeJob, OutboxEvent
from app.models.user import User
from app.repositories.knowledge_repo import KnowledgeChunkRepository
from app.services.knowledge_index_service import KnowledgeEmbeddingService, KnowledgeLexicalStore
from app.services.knowledge_job_runtime import KnowledgeJobWorker, utcnow
from app.services.knowledge_parser_service import ParseResult


@unittest.skipUnless(os.getenv("TEST_POSTGRES_URL"), "需要隔离 PostgreSQL 和 Redis")
class KnowledgeRecoveryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["TEST_POSTGRES_URL"])
        name = f"aiws_knowledge_recovery_{uuid4().hex[:12]}"
        admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
        cls.addClassCleanup(admin.dispose)

        def run_ddl(statement):
            with admin.connect() as connection:
                connection.execute(text(statement))

        run_ddl(f'CREATE DATABASE "{name}"')
        cls.addClassCleanup(run_ddl, f'DROP DATABASE "{name}" WITH (FORCE)')
        engine = create_engine(url.set(database=name))
        cls.addClassCleanup(engine.dispose)
        upgrade_database(engine)
        cls.sessions = sessionmaker(engine, expire_on_commit=False)
        cls.redis = Redis.from_url(settings.knowledge_redis_url, decode_responses=True)
        cls.addClassCleanup(cls.redis.close)
        cls.redis.ping()

    def setUp(self):
        self.stream = f"aiws:knowledge:test:{uuid4().hex}"
        self.addCleanup(self.redis.delete, self.stream)
        self.set_setting("knowledge_job_stream", self.stream)
        self.a = KnowledgeJobWorker(self.redis, session_factory=self.sessions, worker_id="worker-a")
        self.b = KnowledgeJobWorker(self.redis, session_factory=self.sessions, worker_id="worker-b")
        self.a.ensure_group()
        with self.sessions() as db:
            self.user = User(email=f"{uuid4().hex}@knowledge.test", username=f"知识库测试-{uuid4().hex[:12]}")
            db.add(self.user)
            db.flush()
            self.base = KnowledgeBase(user_id=self.user.id, name="测试", embedding_dimensions=4)
            db.add(self.base)
            db.commit()

    def set_setting(self, name, value):
        self.addCleanup(object.__setattr__, settings, name, getattr(settings, name))
        object.__setattr__(settings, name, value)

    def add_job(self, *, kind="parse_document", published=False, enqueue=True, attempts=3, name="fixture"):
        with self.sessions() as db:
            doc = KnowledgeDocument(user_id=self.user.id, knowledge_base_id=self.base.id, file_name=f"{name}.md",
                storage_key=f"{self.user.id}/{name}.md", parse_status="parsed" if kind == "index_document" else "queued",
                parsed_markdown_path=f"{self.user.id}/{name}.md" if kind == "index_document" else None)
            db.add(doc)
            db.flush()
            job = KnowledgeJob(user_id=self.user.id, knowledge_base_id=self.base.id, document_id=doc.id, job_type=kind,
                status="pending", available_at=utcnow() - timedelta(minutes=5), max_attempts=attempts)
            db.add(job)
            db.flush()
            if enqueue:
                db.add(OutboxEvent(aggregate_id=job.id, event_key=f"knowledge-job:{job.id}", payload_json="{}",
                    status="published" if published else "pending", available_at=job.available_at,
                    published_at=job.available_at if published else None))
            db.commit()
            return job

    def test_stream_loss_recovers_published_job_and_only_one_worker_claims(self):
        delayed = self.add_job()
        self.assertTrue(self.a.publisher.publish_once())
        self.assertEqual(self.redis.xlen(self.stream), 1)
        with self.sessions() as db:
            event = db.query(OutboxEvent).filter_by(aggregate_id=delayed.id).one()
            self.assertEqual(event.status, "published")
            event.published_at = utcnow() - timedelta(minutes=5)
            db.commit()
        uploaded_only = self.add_job(enqueue=False)
        healthy = self.add_job(published=True)
        self.assertEqual(self.a.coordinator.claim(healthy.id, self.a.worker_id).state, "claimed")
        self.assertEqual(self.a.coordinator.claim(healthy.id, self.a.worker_id).state, "busy")
        self.redis.delete(self.stream)
        with ThreadPoolExecutor(2) as pool:
            self.assertEqual(sum(pool.map(lambda worker: worker.coordinator.reconcile(), (self.a, self.b))), 1)
        self.assertTrue(self.a.publisher.publish_once())
        self.assertFalse(self.b.publisher.publish_once())
        self.a._execute_parse = lambda lease: ParseResult("recovered.md", "ok")
        self.a._poll_once()  # 收到 NOGROUP 并重建消费组。
        self.a._poll_once()
        with self.sessions() as db:
            self.assertEqual(db.get(KnowledgeJob, delayed.id).status, "succeeded")
            self.assertEqual(db.get(KnowledgeJob, delayed.id).retry_count, 1)
            self.assertEqual(db.get(KnowledgeJob, uploaded_only.id).retry_count, 0)
            self.assertEqual(db.get(KnowledgeJob, healthy.id).status, "running")
        self.assertEqual(self.redis.xpending(self.stream, settings.knowledge_job_group)["pending"], 0)

    def test_expired_attempts_requeue_or_dead_letter_and_fence_old_results(self):
        retryable = self.add_job(published=True)
        exhausted = self.add_job(published=True, attempts=1)
        leases = [self.a.coordinator.claim(job.id, self.a.worker_id).lease for job in (retryable, exhausted)]
        with self.sessions() as db:
            for lease in leases:
                db.get(KnowledgeJob, lease.job_id).lease_expires_at = utcnow() - timedelta(seconds=1)
            db.commit()
        self.assertEqual(self.b.coordinator.reconcile(), 2)
        for lease in leases:
            self.assertFalse(self.a.coordinator.complete_parse(lease, ParseResult("expired.md", "old")))
        with self.sessions() as db:
            pending = db.get(KnowledgeJob, retryable.id)
            self.assertEqual((pending.status, pending.retry_count), ("pending", 1))
            self.assertEqual(db.get(KnowledgeJob, exhausted.id).status, "dead_letter")
            self.assertEqual(db.query(OutboxEvent).filter_by(aggregate_id=retryable.id, status="pending").count(), 1)

    def test_two_ready_generations_rebase_and_final_snapshot_keeps_both_documents(self):
        first = self.add_job(kind="index_document", published=True, name="kafka")
        second = self.add_job(kind="index_document", published=True, name="redis")

        async def embed(adapter, *, user_id, knowledge_base, texts):
            return [[1., 0., 0., 0.] for _ in texts]

        with TemporaryDirectory() as area, patch.object(KnowledgeEmbeddingService, "embed_texts", embed):
            self.set_setting("upload_dir", area)
            self.set_setting("knowledge_index_dir", area)
            for name in ("kafka", "redis"):
                path = Path(area) / self.user.id / f"{name}.md"
                path.parent.mkdir(exist_ok=True)
                path.write_text(f"# {name}\n\n{name} 检索测试材料。", encoding="utf8")
            lease_a = self.a.coordinator.claim(first.id, self.a.worker_id).lease
            original_index = self.b._execute_index

            def conflicting_build(lease_b):
                result_b = original_index(lease_b)
                generation_a, result_a = self.a._execute_index(lease_a)
                self.assertEqual(self.a.coordinator.activate_generation(lease_a, generation_a, result_a), "succeeded")
                return result_b

            self.b._execute_index = conflicting_build
            initial_message = self.redis.xadd(self.stream, {"job_id": second.id})
            self.redis.xreadgroup(settings.knowledge_job_group, self.b.worker_id, {self.stream: ">"}, count=1)
            self.assertEqual(self.b.process_message(initial_message, {"job_id": second.id}), "generation_conflict")
            self.assertEqual(self.redis.xpending(self.stream, settings.knowledge_job_group)["pending"], 0)
            with self.sessions() as db:
                pending = db.get(KnowledgeJob, second.id)
                self.assertEqual((pending.status, pending.error_code), ("pending", "generation_conflict"))
                self.assertEqual(db.get(KnowledgeIndexGeneration, f"{second.id}-v1").status, "superseded")
                event = db.query(OutboxEvent).filter_by(aggregate_id=second.id, status="pending").order_by(OutboxEvent.created_at.desc()).first()
                event.available_at = pending.available_at = utcnow()
                db.commit()
            self.b._execute_index = original_index
            while self.a.publisher.publish_once():
                pass
            while self.redis.xinfo_groups(self.stream)[0]["lag"]:
                self.b._poll_once()
            with self.sessions() as db:
                self.assertEqual(db.get(KnowledgeJob, second.id).status, "succeeded")
                active = db.get(KnowledgeBase, self.base.id).active_index_generation
                chunks = KnowledgeChunkRepository(db).list_by_knowledge_base(self.base.id, self.user.id,
                    index_generation=active)
                self.assertEqual({c.document_id for c in chunks}, {first.document_id, second.document_id})
                lexical = KnowledgeLexicalStore()._load(knowledge_base_id=self.base.id, generation_id=active)
                self.assertEqual(set(lexical["chunk_refs"]), {str(c.vector_id) for c in chunks})
