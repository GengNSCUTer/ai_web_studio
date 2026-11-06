"""后台图的敏感状态边界、租约续期与索引半成品/ACK业务回归。"""

import asyncio
from datetime import timedelta
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from backend.tests import test_memory_automation as memory_fixture
from backend.tests import test_knowledge_job_runtime as knowledge_fixture
from app.core.config import settings
from app.models.conversation import Conversation
from app.models.knowledge import KnowledgeJob, KnowledgeIndexGeneration, OutboxEvent
from app.repositories.knowledge_repo import KnowledgeBaseRepository
from app.models.user_memory import MemoryExtractionJob, UserMemory
from app.services.memory_candidate_runtime import MemoryCandidateWorker, utcnow
from app.services.agent_graph.memory_extraction_graph import build_memory_extraction_graph, MemoryExtractionContext
from app.services.knowledge_job_runtime import KnowledgeJobWorker
from app.services.knowledge_index_service import KnowledgeEmbeddingService


class MemoryExtractionGraphTest(unittest.TestCase):
    setUp = memory_fixture.MemoryAutomationTest.setUp
    tearDown = memory_fixture.MemoryAutomationTest.tearDown
    turn = memory_fixture.MemoryAutomationTest.turn
    enqueue = memory_fixture.MemoryAutomationTest.enqueue
    candidate = memory_fixture.MemoryAutomationTest.candidate

    def test_graph_updates_exclude_model_text_secret_and_suggestions_but_commit_cursor(self):
        job = self.enqueue()
        worker = MemoryCandidateWorker(session_factory=self.sessions, owner="graph-memory",
            provider_service=SimpleNamespace(complete_chat=AsyncMock(return_value=json.dumps([self.candidate()], ensure_ascii=False))))
        job_id, version = worker._claim()
        original = worker._load_snapshot
        def snapshot(*args):
            value = original(*args)
            value["provider_call"]["api_key"] = "synthetic-memory-secret"
            return value
        worker._load_snapshot = snapshot
        async def collect():
            return [update async for update in build_memory_extraction_graph().astream(
                {"job_id": job_id, "lease_version": version}, context=MemoryExtractionContext(worker, job_id, version),
                stream_mode="updates")]
        updates = asyncio.run(collect())
        self.assertEqual([next(iter(update)) for update in updates], ["snapshot", "extract", "verify", "conflicts", "persist", "backlog"])
        text = json.dumps(updates, ensure_ascii=False)
        self.assertNotIn("synthetic-memory-secret", text)
        self.assertNotIn("PostgreSQL", text)
        with self.sessions() as db:
            self.assertEqual(db.get(MemoryExtractionJob, job.id).status, "succeeded")
            self.assertEqual(db.get(Conversation, self.conv).memory_extraction_cursor, 1)
            self.assertEqual(db.query(UserMemory).count(), 1)

    def test_heartbeat_extends_lease_while_waiting_for_model(self):
        job = self.enqueue()
        seen = []
        async def provider(**kwargs):
            with self.sessions() as db:
                seen.append(db.get(MemoryExtractionJob, job.id).lease_expires_at)
            await asyncio.sleep(0.15)
            with self.sessions() as db:
                seen.append(db.get(MemoryExtractionJob, job.id).lease_expires_at)
            return "[]"
        worker = MemoryCandidateWorker(session_factory=self.sessions,
            provider_service=SimpleNamespace(complete_chat=provider))
        worker.HEARTBEAT_SECONDS = 0.02
        self.assertTrue(asyncio.run(worker.run_once()))
        self.assertGreater(seen[1], seen[0])

    def test_lease_takeover_cancels_old_model_and_cannot_advance_cursor(self):
        job = self.enqueue()
        cancelled = []
        async def provider(**kwargs):
            with self.sessions() as db:
                row = db.get(MemoryExtractionJob, job.id)
                row.lease_owner = "replacement"
                row.lease_version += 1
                db.commit()
            try:
                await asyncio.sleep(5)
            finally:
                cancelled.append(True)
            return "[]"
        worker = MemoryCandidateWorker(session_factory=self.sessions,
            provider_service=SimpleNamespace(complete_chat=provider))
        worker.HEARTBEAT_SECONDS = 0.01
        self.assertTrue(asyncio.run(worker.run_once()))
        self.assertTrue(cancelled)
        with self.sessions() as db:
            self.assertEqual(db.get(MemoryExtractionJob, job.id).lease_owner, "replacement")
            self.assertEqual(db.get(Conversation, self.conv).memory_extraction_cursor, 0)
            self.assertEqual(db.query(UserMemory).count(), 0)

    def test_expired_but_not_reclaimed_memory_lease_cannot_save(self):
        job = self.enqueue()
        worker = MemoryCandidateWorker(session_factory=self.sessions)
        job_id, version = worker._claim()
        with self.sessions() as db:
            db.get(MemoryExtractionJob, job.id).lease_expires_at = utcnow() - timedelta(seconds=1)
            db.commit()
        self.assertFalse(worker._complete(job_id, version, []))
        with self.sessions() as db:
            self.assertEqual(db.get(Conversation, self.conv).memory_extraction_cursor, 0)


class KnowledgeIndexGraphTest(unittest.TestCase):
    _job = knowledge_fixture.KnowledgeJobRuntimeTest._job

    def setUp(self):
        knowledge_fixture.KnowledgeJobRuntimeTest.setUp(self)
        self.upload = tempfile.TemporaryDirectory()
        self.index = tempfile.TemporaryDirectory()
        self.previous = settings.upload_dir, settings.knowledge_index_dir
        object.__setattr__(settings, "upload_dir", self.upload.name)
        object.__setattr__(settings, "knowledge_index_dir", self.index.name)
        relative = f"{self.user.id}/knowledge/{self.knowledge_base.id}/{self.document.id}.md"
        path = Path(self.upload.name) / relative
        path.parent.mkdir(parents=True)
        path.write_text("# 图索引验收\n\n测试知识库使用PostgreSQL保存向量，Redis投递任务。", encoding="utf-8")
        self.document.parse_status, self.document.parsed_markdown_path = "parsed", relative
        self.db.commit()

    def tearDown(self):
        object.__setattr__(settings, "upload_dir", self.previous[0])
        object.__setattr__(settings, "knowledge_index_dir", self.previous[1])
        self.upload.cleanup()
        self.index.cleanup()
        knowledge_fixture.KnowledgeJobRuntimeTest.tearDown(self)

    def embedding(self, *, user_id, knowledge_base, texts):
        self.assertFalse(self.active_db_transaction(), "Embedding期间仍持有索引业务事务")
        return [[1.0] + [0.0] * (knowledge_base.embedding_dimensions - 1) for _ in texts]

    def active_db_transaction(self):
        # 监听实际构建Session，不从测试夹具自己的只读Session判断。
        return any(db.in_transaction() for db in self.live_sessions)

    def worker(self, redis):
        self.live_sessions = []
        def factory():
            db = self.SessionLocal()
            self.live_sessions.append(db)
            return db
        return KnowledgeJobWorker(redis, session_factory=factory, worker_id="graph-index")

    def test_actual_index_graph_commits_generation_before_redis_ack(self):
        job = self._job("index_document")
        observed = []
        outer = self
        class Redis(knowledge_fixture.FakeRedis):
            def xack(self, *args):
                with outer.SessionLocal() as db:
                    observed.append(db.get(KnowledgeJob, job.id).status)
                return super().xack(*args)
        redis = Redis()
        worker = self.worker(redis)
        with patch("app.services.knowledge_index_service.KnowledgeEmbeddingService.embed_texts", new_callable=AsyncMock,
                   side_effect=self.embedding):
            self.assertEqual(worker.process_message("1-0", {"job_id": job.id}), "succeeded")
            self.assertEqual(worker.process_message("1-0", {"job_id": job.id}), "terminal")
        self.assertEqual(observed, ["succeeded", "succeeded"])
        with self.SessionLocal() as db:
            self.assertEqual(db.get(KnowledgeIndexGeneration, f"{job.id}-v1").status, "active")

    def test_bm25_failure_keeps_active_generation_and_marks_inactive_failed(self):
        job = self._job("index_document")
        redis = knowledge_fixture.FakeRedis()
        worker = self.worker(redis)
        with patch("app.services.knowledge_index_service.KnowledgeEmbeddingService.embed_texts", new_callable=AsyncMock,
                   side_effect=self.embedding), patch("app.services.knowledge_index_service.KnowledgeLexicalStore.rebuild",
                                                     side_effect=RuntimeError("测试BM25失败")):
            self.assertEqual(worker.process_message("1-0", {"job_id": job.id}), "retry_or_dead_letter")
        with self.SessionLocal() as db:
            self.assertEqual(db.get(KnowledgeIndexGeneration, f"{job.id}-v1").status, "failed")
            self.assertEqual(db.get(type(self.knowledge_base), self.knowledge_base.id).active_index_generation, "legacy")
            self.assertEqual(db.get(KnowledgeJob, job.id).status, "pending")
        self.assertEqual(len(redis.acked), 1)

    def test_real_embedding_dispatch_reads_frozen_settings_without_reopening_transaction(self):
        job = self._job("index_document")
        self.SessionLocal.configure(expire_on_commit=True)
        worker = self.worker(knowledge_fixture.FakeRedis())
        async def model(service, *, user_id, knowledge_base, texts):
            config = service.setting_service.get_or_create_user_settings(user_id)
            _ = config.knowledge_embedding_base_url
            return self.embedding(user_id=user_id, knowledge_base=knowledge_base, texts=texts)
        with patch.object(KnowledgeEmbeddingService, "_embed_openai_compatible", autospec=True, side_effect=model):
            self.assertEqual(worker.process_message("1-0", {"job_id": job.id}), "succeeded")

    def test_delete_indexed_base_removes_its_generations_and_only_its_outbox(self):
        job = self._job("index_document")
        worker = self.worker(knowledge_fixture.FakeRedis())
        with patch("app.services.knowledge_index_service.KnowledgeEmbeddingService.embed_texts", new_callable=AsyncMock,
                   side_effect=self.embedding):
            self.assertEqual(worker.process_message("1-0", {"job_id": job.id}), "succeeded")
        self.db.add(OutboxEvent(event_key="other-event", aggregate_id="other-aggregate", payload_json="{}"))
        self.db.add(OutboxEvent(event_key="own-event", aggregate_id=job.id, payload_json="{}"))
        self.db.commit()
        KnowledgeBaseRepository(self.db).delete(self.knowledge_base)
        self.assertEqual(self.db.query(KnowledgeIndexGeneration).count(), 0)
        self.assertEqual([row.aggregate_id for row in self.db.query(OutboxEvent)], ["other-aggregate"])
