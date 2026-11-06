"""真实 PostgreSQL 验证索引召回、旧事实、修订号和后台补建，不使用线上资料。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import app.models  # noqa: F401
from sqlalchemy import create_engine, event, insert, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models.conversation import Conversation
from app.models.project import Project
from app.models.user import User
from app.models.user_memory import UserMemory
from app.models.user_setting import UserSetting
from app.repositories.memory_repo import UserMemoryRepository
from app.services.memory_index_service import MemoryIndexWorker
from app.services.memory_read_cache import MemoryReadCache
from app.services.memory_retrieval_service import MemoryRetrievalService, text_hash
from backend.tests.test_chat_memory_capacity import FakeRedis


@unittest.skipUnless(os.getenv("TEST_POSTGRES_URL"), "需要隔离 PostgreSQL/pgvector 集成环境")
class IndexedMemoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["TEST_POSTGRES_URL"])
        cls.name = "aiws_indexed_memory_" + uuid4().hex
        cls.admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
        with cls.admin.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{cls.name}"'))
        cls.engine = create_engine(url.set(database=cls.name))
        with cls.engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION vector"))
        Base.metadata.create_all(cls.engine)
        cls.sessions = sessionmaker(cls.engine, expire_on_commit=False)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()
        with cls.admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE "{cls.name}" WITH (FORCE)'))
        cls.admin.dispose()

    def setUp(self):
        with self.sessions() as db:
            user = User(username=uuid4().hex, email=f"{uuid4().hex}@example.test")
            db.add(user)
            db.flush()
            self.uid = user.id
            projects = [Project(user_id=self.uid, name=name) for name in ("a", "b")]
            db.add_all(projects)
            db.flush()
            self.a, self.b = [p.id for p in projects]
            db.add(UserSetting(user_id=self.uid, memory_enabled=True, knowledge_embedding_dimensions=1024))
            db.commit()
        self.config = dict(provider="siliconflow", model="BAAI/bge-m3", dimensions=1024,
                           base_url="https://api.siliconflow.cn/v1")
        self.signature = MemoryRetrievalService.signature(self.config)
        self.vector = [1.] + [0.] * 1023
        self.other_vector = [0., 1.] + [0.] * 1022
        self.embedding = SimpleNamespace(embed_texts=AsyncMock(side_effect=lambda **kw:
            [self.vector for _ in kw["texts"]]))
        self.cache = MemoryReadCache(FakeRedis(), enabled=True)
        self.reader = MemoryRetrievalService(session_factory=self.sessions, embedding_service=self.embedding,
                                             read_cache=self.cache)

    def memory(self, *, indexed=True, **changes):
        values = dict(id=str(uuid4()), user_id=self.uid, version=1, title="旅行偏好", content="喜欢到雪山滑雪",
                      memory_type="fact", project_id=self.a, status="active", is_enabled=True)
        values.update(changes)
        if indexed:
            values.update(embedding_vector=json.dumps(self.vector), embedding_signature=self.signature,
                          embedding_text_hash=text_hash(SimpleNamespace(**values)))
        with self.sessions() as db:
            db.add(UserMemory(**values))
            db.commit()
        return values["id"]

    def retrieve(self, query="冬季假期去哪", project=None):
        return asyncio.run(self.reader.retrieve(user_id=self.uid, project_id=project or self.a,
                                               query=query, max_chars=4000))

    def test_old_semantic_fact_is_found_beyond_latest_hundred(self):
        target = self.memory(updated_at=datetime(2000, 1, 1, tzinfo=timezone.utc))
        values = []
        for i in range(600):
            row = dict(id=str(uuid4()), user_id=self.uid, version=1, title=f"工作记录{i}", content=f"事项编号{i}",
                memory_type="fact", project_id=self.a, status="active", is_enabled=True,
                embedding_signature=self.signature, embedding_vector=json.dumps(self.other_vector))
            row["embedding_text_hash"] = text_hash(SimpleNamespace(**row))
            values.append(row)
        with self.sessions() as db:
            db.execute(insert(UserMemory), values)
            db.commit()
        result = self.retrieve()
        self.assertEqual(result.records[0]["id"], target)
        self.assertLessEqual(result.diagnostics["read_rows"], 104)
        self.assertTrue(result.diagnostics["database_candidates_bounded"])
        self.assertEqual(self.embedding.embed_texts.call_args.kwargs["texts"], ["冬季假期去哪"])

    def test_cache_uses_constant_size_revision_and_no_full_manifest(self):
        mid = self.memory()
        self.retrieve()
        with self.sessions() as db:
            token = UserMemoryRepository(db).retrieval_metadata(self.uid, self.a)
        self.assertEqual(len(token), 2)
        with patch.object(UserMemoryRepository, "retrieval_candidates", side_effect=AssertionError("缓存不应重召回")):
            self.assertEqual(self.retrieve().records[0]["id"], mid)
        self.assertEqual(self.embedding.embed_texts.call_count, 1)

    def test_scope_move_invalidates_cached_global_legacy_memory(self):
        with self.sessions() as db:
            conv = Conversation(user_id=self.uid, title="来源", project_id=self.a, model_name="synthetic")
            db.add(conv)
            db.commit()
            cid = conv.id
        self.memory(project_id=None, source_conversation_id=cid)
        self.assertTrue(self.retrieve().records)
        with self.sessions() as db:
            db.get(Conversation, cid).project_id = self.b
            db.commit()
        self.assertFalse(self.retrieve().records)

    def test_new_fact_invalidates_previously_empty_cache(self):
        self.assertFalse(self.retrieve().records)
        mid = self.memory()
        self.assertEqual(self.retrieve().records[0]["id"], mid)

    def test_expired_temporary_fact_does_not_hide_permanent_baseline(self):
        self.memory(title="旅行偏好", content="今年暑假去海边", expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
        baseline = self.memory(title="长期出游", content="常去雪山滑雪")
        self.retrieve()
        with self.sessions() as db:
            db.execute(update(UserMemory).where(UserMemory.user_id == self.uid,
                UserMemory.expires_at.is_not(None)).values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
            db.commit()
        self.assertIn(baseline, [r["id"] for r in self.retrieve().records])

    def test_malformed_or_stale_json_is_not_a_valid_index(self):
        for raw in ("[true]", "[NaN]", "[0,0]", "not-json"):
            mid = self.memory(indexed=False, embedding_vector=raw, embedding_signature=self.signature,
                              embedding_text_hash="wrong-hash")
            with self.sessions() as db:
                self.assertIsNone(db.scalar(select(UserMemory.search_embedding).where(UserMemory.id == mid)))
        mid = self.memory()
        with self.sessions() as db:
            db.execute(update(UserMemory).where(UserMemory.id == mid).values(content="已修改的事实"))
            db.commit()
            self.assertIsNone(db.scalar(select(UserMemory.search_embedding).where(UserMemory.id == mid)))

    def test_foreground_query_does_not_hold_transaction_during_embedding(self):
        self.memory()
        async def check(**kwargs):
            self.assertEqual(self.engine.pool.checkedout(), 0)
            return [self.vector for _ in kwargs["texts"]]
        self.embedding.embed_texts.side_effect = check
        self.assertTrue(self.retrieve().records)

    def test_parallel_different_questions_do_not_read_all_bodies(self):
        self.memory()
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda i: self.retrieve(f"冬季假期目的地 {i}"), range(16)))
        self.assertTrue(all(r.records for r in results))
        self.assertTrue(all(r.diagnostics["read_rows"] <= 104 for r in results))
        self.assertEqual(self.engine.pool.checkedout(), 0)

    def test_worker_builds_missing_vectors_without_changing_fact_recency(self):
        mid = self.memory(indexed=False)
        with self.sessions() as db:
            row = db.get(UserMemory, mid)
            before = row.version, row.updated_at
            revision = db.scalar(select(User.memory_revision).where(User.id == self.uid))
        worker = MemoryIndexWorker(session_factory=self.sessions, embedding_service=self.embedding)
        # 该隔离库其余测试留下的合规资料也可能排在前面；只限制选取本用户。
        with self.sessions() as db:
            db.execute(update(UserSetting).where(UserSetting.user_id != self.uid).values(memory_enabled=False))
            db.commit()
        self.assertTrue(asyncio.run(worker.run_once()))
        with self.sessions() as db:
            row = db.get(UserMemory, mid)
            self.assertEqual((row.version, row.updated_at), before)
            self.assertIsNotNone(db.scalar(select(UserMemory.search_embedding).where(UserMemory.id == mid)))
            self.assertEqual(db.scalar(select(User.memory_revision).where(User.id == self.uid)), revision)

    def test_worker_late_response_cannot_index_revoked_memory(self):
        mid = self.memory(indexed=False)
        with self.sessions() as db:
            db.execute(update(UserSetting).where(UserSetting.user_id != self.uid).values(memory_enabled=False))
            db.commit()
        async def revoke(**kwargs):
            with self.sessions() as db:
                db.execute(update(UserMemory).where(UserMemory.id == mid).values(status="revoked", is_enabled=False))
                db.commit()
            return [self.vector for _ in kwargs["texts"]]
        worker = MemoryIndexWorker(session_factory=self.sessions,
            embedding_service=SimpleNamespace(embed_texts=AsyncMock(side_effect=revoke)))
        asyncio.run(worker.run_once())
        with self.sessions() as db:
            self.assertIsNone(db.scalar(select(UserMemory.search_embedding).where(UserMemory.id == mid)))

    def test_model_change_invalidates_index_and_allows_background_rebuild(self):
        mid = self.memory()
        with self.sessions() as db:
            db.scalar(select(UserSetting).where(UserSetting.user_id == self.uid)).knowledge_embedding_model = "new-model"
            db.commit()
            self.assertIsNone(db.scalar(select(UserMemory.search_embedding).where(UserMemory.id == mid)))

    def test_revision_rolls_back_with_business_transaction(self):
        mid = self.memory()
        with self.sessions() as db:
            before = db.scalar(select(User.memory_revision).where(User.id == self.uid))
            db.execute(update(UserMemory).where(UserMemory.id == mid).values(status="revoked"))
            db.rollback()
            self.assertEqual(db.scalar(select(User.memory_revision).where(User.id == self.uid)), before)

    def test_native_path_does_not_select_json_vectors(self):
        self.memory()
        statements = []
        def capture(conn, cursor, statement, params, context, many):
            if statement.lstrip().lower().startswith("select"):
                statements.append(statement.lower())
        event.listen(self.engine, "before_cursor_execute", capture)
        try:
            self.assertTrue(self.retrieve().records)
        finally:
            event.remove(self.engine, "before_cursor_execute", capture)
        self.assertFalse(any("user_memories.embedding_vector" in sql for sql in statements))

    def test_clock_expiry_invalidates_cache_without_a_database_write(self):
        mid = self.memory(expires_at=datetime.now(timezone.utc) + timedelta(seconds=.3))
        self.assertEqual(self.retrieve().records[0]["id"], mid)
        time.sleep(.35)
        self.assertFalse(self.retrieve().records)

    def test_index_worker_failure_has_backoff_and_does_not_touch_fact_state(self):
        mid = self.memory(indexed=False)
        with self.sessions() as db:
            db.execute(update(UserSetting).where(UserSetting.user_id != self.uid).values(memory_enabled=False))
            db.commit()
        broken = SimpleNamespace(embed_texts=AsyncMock(side_effect=TimeoutError()))
        worker = MemoryIndexWorker(session_factory=self.sessions, embedding_service=broken)
        self.assertFalse(asyncio.run(worker.run_once()))
        self.assertFalse(asyncio.run(worker.run_once()))
        self.assertEqual(broken.embed_texts.call_count, 1)
        with self.sessions() as db:
            row = db.get(UserMemory, mid)
            self.assertEqual(row.status, "active")
            self.assertGreater(row.embedding_retry_after, datetime.now(timezone.utc))

    def test_twenty_controlled_semantic_targets_recall_without_lexical_overlap(self):
        targets = []
        values = []
        for i in range(320):
            row = dict(id=str(uuid4()), user_id=self.uid, version=1, title=f"独立档案{i}",
                content=f"已核实的记录编号{i}", memory_type="fact", project_id=self.a,
                status="active", is_enabled=True, embedding_signature=self.signature)
            vector = [0.] * 1024
            vector[i if i < 20 else 1023] = 1.
            row["embedding_vector"] = json.dumps(vector)
            row["embedding_text_hash"] = text_hash(SimpleNamespace(**row))
            if i < 20:
                targets.append((row["id"], vector))
            values.append(row)
        with self.sessions() as db:
            db.execute(insert(UserMemory), values)
            db.commit()
        for i, (mid, vector) in enumerate(targets):
            self.embedding.embed_texts.side_effect = None
            self.embedding.embed_texts.return_value = [vector]
            result = self.retrieve(f"请回顾户外活动主题 {i}")
            self.assertEqual(result.records[0]["id"], mid)
            self.assertLessEqual(result.diagnostics["read_rows"], 104)

    def test_incomplete_embedding_batch_does_not_freeze_a_partial_query_cache(self):
        for i in range(40):
            self.memory(indexed=False, title=f"档案{i}", content=f"独立事项编号{i}")
        first = self.retrieve()
        self.assertEqual(first.diagnostics["embedding_generated"], 32)
        self.assertTrue(first.diagnostics["index_backlog_pending"])
        second = self.retrieve()
        self.assertNotEqual(second.diagnostics["read_cache"], "hit")
        self.assertEqual(second.diagnostics["embedding_generated"], 8)
        self.assertEqual(self.retrieve().diagnostics["read_cache"], "hit")


if __name__ == "__main__":
    unittest.main()
