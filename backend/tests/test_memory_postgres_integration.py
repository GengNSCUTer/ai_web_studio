"""用独立 PostgreSQL 测试库验证锁、版本和事务回滚，不接触用户真实记忆。"""

from concurrent.futures import ThreadPoolExecutor
import asyncio
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import os
from threading import Barrier
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.core.database import Base
from app.models.user import User
from app.models.user_setting import UserSetting
from app.models.user_memory import UserMemory
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.project import Project
from app.models.user_memory import MemoryExtractionJob, MemoryMaintenanceJob
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.project_repo import ProjectRepository
from app.repositories.memory_repo import UserMemoryRepository
from app.schemas.conversation import ConversationUpdate
from app.schemas.memory import UserMemoryCreate, UserMemoryUpdate, MemoryMaintenanceReview, MemorySuggestion
from app.services.memory_service import MemoryService
from app.services.memory_candidate_runtime import MemoryCandidateWorker, MemoryExtractionJobService
from app.services.memory_extraction_policy import source_snapshot
from app.services.memory_maintenance_service import MemoryMaintenanceService
from app.services.conversation_service import ConversationService
from app.services.memory_retrieval_service import MemoryRetrievalService
from app.services.setting_service import SettingService
from app.repositories.setting_repo import UserSettingRepository
from app.services.memory_conflict_service import MemoryConflictService


TEST_POSTGRES_URL = os.getenv("TEST_POSTGRES_URL")


@unittest.skipUnless(TEST_POSTGRES_URL, "需要 TEST_POSTGRES_URL 才能验证 PostgreSQL 并发事务")
class MemoryPostgresIntegrationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(TEST_POSTGRES_URL)
        cls.name = f"aiws_memory_test_{uuid4().hex}"
        cls.admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
        with cls.admin.connect() as conn:
            conn.execute(text(f'create database "{cls.name}"'))
        cls.engine = create_engine(url.set(database=cls.name))
        with cls.engine.begin() as conn:
            conn.execute(text("create extension if not exists vector"))
        Base.metadata.create_all(cls.engine)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        with cls.admin.connect() as conn:
            conn.execute(text(f'drop database "{cls.name}" with (force)'))
        cls.admin.dispose()

    def setUp(self):
        with Session(self.engine) as db:
            user = User(username=f"memory-{uuid4().hex[:12]}", email=f"{uuid4()}@example.test")
            db.add(user)
            db.commit()
            self.user_id = user.id
            old = MemoryService(UserMemoryRepository(db)).create_memory(
                self.user_id, UserMemoryCreate(memory_type="profile", title="回答语言", content="默认中文回答"))
            self.old_id = old.id
            candidates = [UserMemory(user_id=self.user_id, memory_type="profile", title="回答语言",
                                     content="默认英文回答", status="pending", is_enabled=False,
                                     supersedes_memory_id=old.id, risk_level="conflict") for _ in range(2)]
            db.add_all(candidates)
            db.commit()
            self.candidate_ids = [row.id for row in candidates]

    def approvals(self, ids):
        barrier = Barrier(2)
        def approve(memory_id):
            with Session(self.engine, expire_on_commit=False) as db:
                repo = UserMemoryRepository(db)
                memory = repo.get_by_user(memory_id, self.user_id)
                # 两个请求先读到相同旧快照，再同时进入写入事务。
                barrier.wait(timeout=10)
                try:
                    result = MemoryService(repo).approve_candidate(memory=memory)
                    return ("ok", result.id)
                except ValueError:
                    return ("conflict", memory_id)
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(approve, ids))

    def memory_project(self):
        with Session(self.engine) as db:
            project = Project(user_id=self.user_id, name="资料库")
            db.add_all([project, UserSetting(user_id=self.user_id, memory_enabled=True,
                memory_auto_candidate_enabled=True, memory_auto_activate_enabled=True)])
            db.commit()
            return project.id

    def extraction(self, project, content):
        with Session(self.engine) as db:
            conversation = Conversation(user_id=self.user_id, project_id=project, title="来源", model_name="test")
            db.add(conversation)
            db.flush()
            message = Message(conversation_id=conversation.id, sequence=1, role="user", content=content)
            db.add(message)
            db.flush()
            job = MemoryExtractionJob(user_id=self.user_id, conversation_id=conversation.id, project_id=project,
                idempotency_key=uuid4().hex, source_message_ids=message.id, source_snapshot=source_snapshot([message]),
                status="running", lease_owner="test-worker", lease_version=1, attempts=1,
                lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=2))
            db.add(job)
            db.commit()
            return job.id, message.id, conversation.id

    @staticmethod
    def database_suggestion(message_id, content):
        return MemorySuggestion(memory_type="project", title="项目数据库", content=content, confidence="high",
                                source_message_id=message_id, evidence_quote=content)

    def test_opposite_worker_completion_orders_leave_both_facts_pending(self):
        project = self.memory_project()
        sessions = sessionmaker(self.engine)
        worker = MemoryCandidateWorker(session_factory=sessions, owner="test-worker")
        for pair in (("PostgreSQL", "MySQL"), ("MySQL", "PostgreSQL")):
            for name in pair:
                content = f"项目数据库使用 {name}。"
                job, message, _ = self.extraction(project, content)
                self.assertTrue(worker._complete(job, 1, [self.database_suggestion(message, content)]))
            with sessions() as db:
                rows = list(db.scalars(select(UserMemory).where(UserMemory.project_id == project,
                    UserMemory.status == "pending")))
                self.assertEqual({row.content for row in rows}, {f"项目数据库使用 {name}。" for name in pair})
                self.assertTrue(all(not row.is_enabled and row.needs_explicit_choice for row in rows))
                service = MemoryService(UserMemoryRepository(db), ConversationRepository(db))
                with self.assertRaisesRegex(ValueError, "明确确认"):
                    service.approve_candidate(memory=rows[0])
                for row in rows:
                    db.delete(row)
                db.commit()

    def test_manually_confirmed_fact_survives_a_late_contradiction(self):
        project = self.memory_project()
        sessions = sessionmaker(self.engine)
        worker = MemoryCandidateWorker(session_factory=sessions, owner="test-worker")
        old, old_msg, _ = self.extraction(project, "项目数据库使用 PostgreSQL。")
        self.assertTrue(worker._complete(old, 1, [self.database_suggestion(old_msg, "项目数据库使用 PostgreSQL。")]))
        with sessions() as db:
            row = db.scalar(select(UserMemory).where(UserMemory.extraction_job_id == old))
            approved = MemoryService(UserMemoryRepository(db), ConversationRepository(db)).approve_candidate(memory=row)
            self.assertEqual(approved.source, "auto_candidate")
        new, new_msg, _ = self.extraction(project, "项目数据库使用 MySQL。")
        self.assertTrue(worker._complete(new, 1, [self.database_suggestion(new_msg, "项目数据库使用 MySQL。")]))
        with sessions() as db:
            self.assertEqual(db.get(UserMemory, approved.id).status, "active")
            self.assertEqual(db.scalar(select(UserMemory.status).where(UserMemory.extraction_job_id == new)), "pending")

    def test_edit_move_delete_invalidate_source_and_independent_save_preserves_history(self):
        project = self.memory_project()
        sessions = sessionmaker(self.engine)
        worker = MemoryCandidateWorker(session_factory=sessions, owner="test-worker")
        original = "项目数据库使用 PostgreSQL。"
        job, message, conversation = self.extraction(project, original)
        self.assertTrue(worker._complete(job, 1, [self.database_suggestion(message, original)]))
        with sessions() as db:
            service = MemoryService(UserMemoryRepository(db), ConversationRepository(db))
            row = db.scalar(select(UserMemory).where(UserMemory.extraction_job_id == job))
            db.get(Message, message).content = "项目数据库使用 MySQL。"
            db.commit()
            self.assertEqual(service._memory_response(row, self.user_id).source_issue, "source_changed")
            with self.assertRaisesRegex(ValueError, "来源已失效"):
                service.approve_candidate(memory=row)
            with self.assertRaisesRegex(ValueError, "来源已失效"):
                service.update_memory(memory=row, payload=UserMemoryUpdate(is_enabled=True))
            reextract = MemoryExtractionJobService(db).reextract(self.user_id, row)
            self.assertEqual(MemoryExtractionJobService(db).reextract(self.user_id, row).id, reextract.id)
            reextract.status, reextract.lease_owner = "running", worker.owner
            reextract.lease_version = reextract.attempts = 1
            reextract.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=2)
            db.commit()
            self.assertTrue(worker._complete(reextract.id, 1, [self.database_suggestion(message, "项目数据库使用 MySQL。")]))
            self.assertEqual(db.scalar(select(UserMemory.status).where(UserMemory.extraction_job_id == reextract.id)), "pending")
            standalone = service.save_independent(row, row.version)
            conversations = ConversationService(ConversationRepository(db), ProjectRepository(db))
            conversations.update_conversation(conversation, ConversationUpdate(project_id=None), self.user_id)
            conversations.update_conversation(conversation, ConversationUpdate(project_id=project), self.user_id)
            self.assertEqual(service._memory_response(row, self.user_id).source_issue, "scope_changed")
            self.assertTrue(conversations.delete_conversation(conversation, self.user_id))
            versions = service.history(db.get(UserMemory, standalone.id)).versions
            self.assertEqual({item.id: item.status for item in versions}, {standalone.id: "active", row.id: "superseded"})
            self.assertFalse(next(item for item in versions if item.id == row.id).is_enabled)

    def test_expired_extraction_at_attempt_limit_is_never_claimed_or_written(self):
        project = self.memory_project()
        job_id, _, _ = self.extraction(project, "项目数据库使用 PostgreSQL。")
        sessions = sessionmaker(self.engine)
        with sessions.begin() as db:
            job = db.get(MemoryExtractionJob, job_id)
            job.lease_expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            job.attempts = job.max_attempts
        worker = MemoryCandidateWorker(session_factory=sessions, owner="test-worker")
        claimed = worker._claim()
        self.assertTrue(claimed is None or claimed[0] != job_id)
        self.assertFalse(worker._complete(job_id, 1, []))
        with sessions() as db:
            job = db.get(MemoryExtractionJob, job_id)
            self.assertEqual((job.status, job.error_code, job.attempts), ("failed", "attempts_exhausted", job.max_attempts))

    def test_project_maintenance_worker_requires_review_and_rejects_stale_and_cross_user(self):
        project = self.memory_project()
        sessions = sessionmaker(self.engine, expire_on_commit=False)
        with sessions.begin() as db:
            target = UserMemory(user_id=self.user_id, project_id=project, memory_type="project", title="资料来源",
                                content="资料来自内部知识库")
            other = UserMemory(user_id=self.user_id, project_id=project, memory_type="project", title="资料来源",
                               content="资料来自内部知识库")
            db.add_all([target, other])
        with sessions() as db:
            service = MemoryMaintenanceService(db)
            job = service.enqueue(self.user_id, project)
            self.assertEqual(service.enqueue(self.user_id, project).id, job.id)
        draft = {"kind": "duplicate", "target_id": target.id, "related_id": other.id, "content": ""}
        provider = SimpleNamespace(complete_chat=AsyncMock(return_value="```JSON\r\n" + json.dumps({
            "proposals": [{"kind": "unknown"}, draft]}) + "\r\n```"))
        worker = MemoryCandidateWorker(session_factory=sessions, owner="test-worker", provider_service=provider)
        self.assertTrue(asyncio.run(worker._run_maintenance()))
        with sessions() as db:
            service = MemoryMaintenanceService(db)
            result = service.preview(service.get(self.user_id, job.id))
            self.assertEqual((result.status, result.reviewed_count, result.accepted_count), ("ready", 0, 0))
            self.assertEqual(db.get(UserMemory, other.id).status, "active")
            self.assertIsNotNone(result.estimated_input_tokens)
            with self.assertRaises(LookupError):
                service.get("another-user", job.id)
            proposal = result.proposals[0]
            decision = MemoryMaintenanceReview(decision="apply", expected_version=result.version)
            MemoryService(service.repo).revoke_memory(memory=db.get(UserMemory, other.id))
            self.assertEqual(service.preview(service.get(self.user_id, job.id)).proposals[0]["status"], "stale")
            with self.assertRaisesRegex(ValueError, "重新整理"):
                service.review(self.user_id, job.id, proposal["id"], decision)
            self.assertEqual(db.get(UserMemory, other.id).status, "revoked")
            db.scalar(select(UserSetting).where(UserSetting.user_id == self.user_id)).memory_enabled = False
            db.commit()
            self.assertEqual(service.preview(service.get(self.user_id, job.id)).proposals, [])
            with self.assertRaises(PermissionError):
                service.review(self.user_id, job.id, proposal["id"], decision)

    def test_approved_maintenance_applies_once_and_links_the_version_history(self):
        project = self.memory_project()
        sessions = sessionmaker(self.engine, expire_on_commit=False)
        with sessions.begin() as db:
            target = UserMemory(user_id=self.user_id, project_id=project, memory_type="project", title="资料来源",
                                content="资料来自内部知识库")
            other = UserMemory(user_id=self.user_id, project_id=project, memory_type="project", title="资料来源",
                               content="资料来自内部知识库")
            db.add_all([target, other])
        with sessions() as db:
            service = MemoryMaintenanceService(db)
            job = service.enqueue(self.user_id, project)
        provider = SimpleNamespace(complete_chat=AsyncMock(return_value=json.dumps([
            {"kind": "duplicate", "target_id": target.id, "related_id": other.id},
        ])))
        self.assertTrue(asyncio.run(MemoryCandidateWorker(session_factory=sessions, provider_service=provider).run_once()))
        with sessions() as db:
            service = MemoryMaintenanceService(db)
            before = service.preview(service.get(self.user_id, job.id))
            proposal = before.proposals[0]["id"]
            decision = MemoryMaintenanceReview(decision="apply", expected_version=before.version)
            after = service.review(self.user_id, job.id, proposal, decision)
            self.assertEqual((after.status, after.reviewed_count, after.accepted_count), ("completed", 1, 1))
            self.assertEqual(service.review(self.user_id, job.id, proposal, decision), after)
            self.assertEqual(db.get(UserMemory, other.id).status, "superseded")
            self.assertEqual({row.id for row in service.memory.history(db.get(UserMemory, target.id)).versions}, {target.id, other.id})
            self.assertEqual(db.get(MemoryMaintenanceJob, job.id).attempts, 1)

    def test_maintenance_corrects_only_with_current_user_evidence(self):
        project = self.memory_project()
        sessions = sessionmaker(self.engine, expire_on_commit=False)
        proof = "项目数据库使用 PostgreSQL 16。"
        source_job, source_id, conversation_id = self.extraction(project, proof)
        with sessions.begin() as db:
            db.get(MemoryExtractionJob, source_job).status = "succeeded"
            target = UserMemory(user_id=self.user_id, project_id=project, memory_type="project", title="项目数据库",
                content="项目数据库使用 PostgreSQL。", source="auto_candidate", source_message_ids=source_id,
                source_conversation_id=conversation_id, extraction_job_id=source_job, evidence_quote=proof)
            db.add(target)
        provider_output = [{"kind": "correct", "target_id": target.id, "source_id": target.id,
                            "content": "改成生产数据库。"},
                           {"kind": "correct", "target_id": target.id, "source_id": target.id, "content": proof}]
        provider = SimpleNamespace(complete_chat=AsyncMock(return_value=json.dumps(provider_output)))
        with sessions() as db:
            job = MemoryMaintenanceService(db).enqueue(self.user_id, project)
        worker = MemoryCandidateWorker(session_factory=sessions, provider_service=provider)
        self.assertTrue(asyncio.run(worker._run_maintenance()))
        with sessions() as db:
            service = MemoryMaintenanceService(db)
            result = service.preview(service.get(self.user_id, job.id))
            self.assertEqual([item["source_quote"] for item in result.proposals], [proof])
            original = db.get(UserMemory, target.id)
            self.assertNotEqual(original.content, proof)
            self.assertNotIn(proof, json.dumps(db.get(MemoryMaintenanceJob, job.id).proposals, ensure_ascii=False))
            updated = service.review(self.user_id, job.id, result.proposals[0]["id"],
                MemoryMaintenanceReview(decision="apply", expected_version=result.version))
            replacement = db.get(UserMemory, updated.proposals[0]["result_memory_id"])
            self.assertEqual((replacement.content, replacement.supersedes_memory_id, replacement.extraction_job_id),
                             (proof, original.id, source_job))
            db.get(Message, source_id).content = "现在改用新数据库。"
            db.commit()
            self.assertEqual(service.memory.source_issues([replacement]), {replacement.id: "source_changed"})

    def test_maintenance_merge_reuses_original_proof_and_archives_both_previous_rows(self):
        project = self.memory_project()
        sessions = sessionmaker(self.engine, expire_on_commit=False)
        proof = "项目数据库使用 PostgreSQL 16。"
        source_job, source_id, conversation_id = self.extraction(project, proof)
        with sessions.begin() as db:
            db.get(MemoryExtractionJob, source_job).status = "succeeded"
            old = UserMemory(user_id=self.user_id, project_id=project, memory_type="project", title="项目数据库",
                             content="项目数据库使用 PostgreSQL。")
            related = UserMemory(user_id=self.user_id, project_id=project, memory_type="project", title="项目数据库",
                content=proof, source="auto_candidate", source_message_ids=source_id,
                source_conversation_id=conversation_id, extraction_job_id=source_job, evidence_quote=proof)
            db.add_all([old, related])
        with sessions() as db:
            job = MemoryMaintenanceService(db).enqueue(self.user_id, project)
        provider = SimpleNamespace(complete_chat=AsyncMock(return_value=json.dumps([{"kind": "merge",
            "target_id": old.id, "related_id": related.id, "source_id": related.id, "content": proof}])))
        self.assertTrue(asyncio.run(MemoryCandidateWorker(session_factory=sessions, provider_service=provider)._run_maintenance()))
        with sessions() as db:
            service = MemoryMaintenanceService(db)
            result = service.preview(service.get(self.user_id, job.id))
            self.assertEqual(result.proposals[0]["source_quote"], proof)
            updated = service.review(self.user_id, job.id, result.proposals[0]["id"],
                MemoryMaintenanceReview(decision="apply", expected_version=result.version))
            replacement = db.get(UserMemory, updated.proposals[0]["result_memory_id"])
            self.assertEqual(replacement.supersedes_memory_id, old.id)
            self.assertEqual(replacement.extraction_job_id, source_job)
            self.assertEqual([db.get(UserMemory, item.id).status for item in (old, related)], ["superseded", "superseded"])

    def test_analyzed_conflict_concurrent_approvals_allow_only_one_replacement(self):
        resolver = MemoryConflictService(session_factory=sessionmaker(self.engine))
        assessed, = asyncio.run(resolver.assess(user_id=self.user_id, project_id=None,
            suggestions=[MemorySuggestion(memory_type='profile', title='回答语言', content='默认英文回答')]))
        with Session(self.engine) as db:
            for candidate in db.scalars(select(UserMemory).where(UserMemory.id.in_(self.candidate_ids))):
                candidate.conflict_review_json = json.dumps(assessed.conflict_review)
            db.commit()
        barrier = Barrier(2)
        def approve(memory_id):
            with Session(self.engine) as db:
                repo = UserMemoryRepository(db)
                row = repo.get_by_user(memory_id, self.user_id)
                barrier.wait(timeout=10)
                try:
                    MemoryService(repo).approve_candidate(memory=row, expected_version=1,
                        supersedes_memory_id=self.old_id, expected_target_version=1, acknowledge_conflicts=True)
                    return 'ok'
                except ValueError:
                    return 'rejected'
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(approve, self.candidate_ids))
        self.assertEqual(sorted(results), ['ok', 'rejected'])
        with Session(self.engine) as db:
            self.assertEqual(db.get(UserMemory, self.old_id).status, 'superseded')
            self.assertEqual(len(list(db.scalars(select(UserMemory).where(UserMemory.user_id == self.user_id,
                                                                        UserMemory.status == 'active')))), 1)

    def test_concurrent_enqueue_creates_one_incremental_job(self):
        with Session(self.engine) as db:
            conv = Conversation(user_id=self.user_id, title="增量并发", model_name="test")
            db.add(conv)
            db.flush()
            user = Message(conversation_id=conv.id, sequence=1, role="user", content="请记住：项目采用 Redis 7")
            assistant = Message(conversation_id=conv.id, sequence=2, role="assistant", content="收到", status="done")
            db.add_all([user, assistant])
            db.commit()
            conv_id, user_id, assistant_id = conv.id, user.id, assistant.id
        barrier = Barrier(2)
        def enqueue(_):
            with Session(self.engine) as db:
                barrier.wait(timeout=10)
                return MemoryExtractionJobService(db).enqueue_after_turn(user_id=self.user_id,
                    conversation_id=conv_id, assistant_message_id=assistant_id, force=True).id
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(enqueue, range(2)))
        self.assertEqual(ids[0], ids[1])
        with Session(self.engine) as db:
            jobs = list(db.scalars(select(MemoryExtractionJob).where(MemoryExtractionJob.conversation_id == conv_id)))
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0].source_message_ids, user_id)
            self.assertEqual(db.get(Conversation, conv_id).memory_extraction_cursor, 0)

    def test_event_enqueue_and_buffer_are_atomic_under_concurrent_turn_hooks(self):
        with Session(self.engine) as db:
            SettingService(UserSettingRepository(db)).get_or_create_user_settings(self.user_id)
            setting = db.scalar(select(UserSetting).where(UserSetting.user_id == self.user_id))
            setting.memory_auto_candidate_enabled = True
            setting.memory_auto_candidate_turn_interval = 4
            conv = Conversation(user_id=self.user_id, model_name="test")
            db.add(conv)
            db.flush()
            first = Message(conversation_id=conv.id, sequence=1, role="user", content="暂定采用 MySQL，尚未决定")
            last = Message(conversation_id=conv.id, sequence=3, role="user", content="我的项目最终决定使用 PostgreSQL。")
            assistant = Message(conversation_id=conv.id, sequence=4, role="assistant", status="done", content="收到")
            db.add_all([first, last, assistant])
            db.commit()
            conv_id, assistant_id, source_ids = conv.id, assistant.id, {first.id, last.id}
        barrier = Barrier(4)
        def enqueue(_):
            with Session(self.engine) as db:
                barrier.wait(timeout=10)
                job = MemoryExtractionJobService(db).enqueue_after_turn(user_id=self.user_id,
                    conversation_id=conv_id, assistant_message_id=assistant_id)
                return job.id
        with ThreadPoolExecutor(max_workers=4) as pool:
            ids = list(pool.map(enqueue, range(4)))
        self.assertEqual(len(set(ids)), 1)
        with Session(self.engine) as db:
            jobs = list(db.scalars(select(MemoryExtractionJob).where(MemoryExtractionJob.conversation_id == conv_id)))
            self.assertEqual(len(jobs), 1)
            self.assertEqual(set(jobs[0].source_message_ids.split(",")), source_ids)
            conv = db.get(Conversation, conv_id)
            self.assertEqual((conv.memory_scan_cursor, conv.memory_extraction_cursor), (3, 0))
            self.assertEqual({row["id"] for row in json.loads(conv.memory_trigger_state)["pending"]}, source_ids)

    def test_two_candidates_cannot_both_replace_same_old_version(self):
        outcomes = self.approvals(self.candidate_ids)
        self.assertEqual(sum(status == "ok" for status, _ in outcomes), 1)
        with Session(self.engine) as db:
            memories = list(db.scalars(select(UserMemory).where(UserMemory.user_id == self.user_id)))
            self.assertEqual(sum(row.status == "active" for row in memories), 1)
            self.assertEqual(sum(row.status == "pending" for row in memories), 1)
            self.assertEqual(db.get(UserMemory, self.old_id).status, "superseded")

    def test_same_candidate_concurrent_confirmation_is_idempotent(self):
        outcomes = self.approvals([self.candidate_ids[0]] * 2)
        self.assertEqual(outcomes, [("ok", self.candidate_ids[0])] * 2)
        with Session(self.engine) as db:
            self.assertEqual(db.get(UserMemory, self.candidate_ids[0]).version, 2)

    def test_stale_parallel_edits_are_fenced_by_version(self):
        barrier = Barrier(2)
        def edit(enabled):
            with Session(self.engine, expire_on_commit=False) as db:
                repo = UserMemoryRepository(db)
                memory = repo.get_by_user(self.old_id, self.user_id)
                barrier.wait(timeout=10)
                try:
                    MemoryService(repo).update_memory(memory=memory, payload=UserMemoryUpdate(is_enabled=enabled))
                    return "ok"
                except ValueError:
                    return "conflict"
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(edit, [True, False]))
        self.assertCountEqual(results, ["ok", "conflict"])

    def test_failure_after_old_version_flush_rolls_back_both_sides(self):
        with Session(self.engine) as db:
            repo = UserMemoryRepository(db)
            memory = repo.get_by_user(self.candidate_ids[0], self.user_id)
            with patch.object(repo, "save", side_effect=RuntimeError("模拟新行保存失败")):
                with self.assertRaises(RuntimeError):
                    MemoryService(repo).approve_candidate(memory=memory)
        with Session(self.engine) as db:
            old, candidate = db.get(UserMemory, self.old_id), db.get(UserMemory, self.candidate_ids[0])
            self.assertEqual(old.status, "active")
            self.assertTrue(old.is_enabled)
            self.assertEqual(old.version, 1)
            self.assertEqual(candidate.status, "pending")
            self.assertFalse(candidate.is_enabled)

    def test_memory_network_phase_holds_no_database_transaction_and_rechecks_revoke(self):
        """真实 PG：网络期间写请求可完成，旧向量结果不能复活已撤销事实。"""
        with Session(self.engine) as db:
            SettingService(UserSettingRepository(db)).get_or_create_user_settings(self.user_id)
            created = MemoryService(UserMemoryRepository(db)).create_memory(self.user_id,
                UserMemoryCreate(title="旅行", content="喜欢雪山度假"))
            memory_id = created.id
        async def embed(**kwargs):
            with self.engine.connect() as connection:
                count = connection.scalar(text("select count(*) from pg_stat_activity where datname=current_database() "
                    "and state='idle in transaction' and pid<>pg_backend_pid()"))
                self.assertEqual(count, 0)
            with Session(self.engine) as db:
                db.execute(text("set local lock_timeout='500ms'"))
                repo = UserMemoryRepository(db)
                MemoryService(repo).revoke_memory(memory=repo.get_by_user(memory_id, self.user_id))
            return [[1.0]+[0.0]*1023 for _ in kwargs["texts"]]
        service = MemoryRetrievalService(session_factory=sessionmaker(self.engine),
            embedding_service=SimpleNamespace(embed_texts=embed))
        result = asyncio.run(service.retrieve(user_id=self.user_id, project_id=None,
            query="冬季滑雪目的地", max_chars=1000))
        self.assertFalse(any(item["id"] == memory_id for item in result.records))
        with Session(self.engine) as db:
            memory = db.get(UserMemory, memory_id)
            self.assertEqual(memory.status, "revoked")
            self.assertIsNone(memory.embedding_vector)


if __name__ == "__main__":
    unittest.main()
