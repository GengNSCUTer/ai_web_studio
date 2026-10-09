"""用独立 PostgreSQL 测试库验证锁、版本和事务回滚，不接触用户真实记忆。"""

from concurrent.futures import ThreadPoolExecutor
import asyncio
from types import SimpleNamespace
import os
from threading import Barrier
import unittest
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

import app.models  # noqa: F401
from app.core.database import Base
from app.models.user import User
from app.models.user_memory import UserMemory
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user_memory import MemoryExtractionJob
from app.repositories.memory_repo import UserMemoryRepository
from app.schemas.memory import UserMemoryCreate, UserMemoryUpdate
from app.services.memory_service import MemoryService
from app.services.memory_candidate_runtime import MemoryExtractionJobService
from app.services.memory_retrieval_service import MemoryRetrievalService
from app.services.setting_service import SettingService
from app.repositories.setting_repo import UserSettingRepository


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
