"""验证有界历史不跳过摘要积压，以及缓存不能绕过 PG 的权限/版本事实。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import os
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import app.models  # noqa: F401
from redis import Redis, RedisError
from sqlalchemy import create_engine, event, insert, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import settings
from app.core.database import Base
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.project import Project
from app.models.user import User
from app.models.user_memory import UserMemory
from app.models.user_setting import UserSetting
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.memory_repo import UserMemoryRepository
from app.repositories.message_repo import MessageRepository
from app.services.chat_context_assembly_service import ChatContextAssemblyService
from app.services.context_governance_service import ContextBudgetPlanner, ContextGovernanceService
from app.services.memory_candidate_runtime import MemoryCandidateWorker, MemoryExtractionJobService
from app.services.memory_read_cache import MemoryReadCache
from app.services.memory_retrieval_service import MemoryRetrievalService
from app.services.memory_service import MemoryService
from app.services.message_service import MessageService


class FakeRedis:
    def __init__(self):
        self.data = {}

    def hget(self, key, field):
        return self.data.get(key, {}).get(field)

    def hset(self, key, field, value):
        self.data.setdefault(key, {})[field] = value

    def eval(self, script, keys, key, field, value, ttl, maximum):
        fields = self.data.setdefault(key, {})
        if field not in fields and len(fields) >= maximum:
            fields.clear()
        fields[field] = value

    def hlen(self, key):
        return len(self.data.get(key, {}))

    def delete(self, key):
        self.data.pop(key, None)


class CapacityCases:
    def setUp(self):
        self.sessions = sessionmaker(self.engine, expire_on_commit=False, autoflush=False)
        self.client = getattr(type(self), "redis_client", None) or FakeRedis()
        self.cache = MemoryReadCache(self.client, enabled=True)
        self.keys = []
        self.original_key = self.cache.key

        def key(db, uid, pid):
            value = self.original_key(db, uid, pid) or f"sqlite-test:{uid}:{pid}"
            self.keys.append(value)
            return value
        self.cache.key = key
        with self.sessions() as db:
            user = User(username=f"capacity-{uuid4().hex[:12]}", email=f"{uuid4()}@example.test")
            db.add(user)
            db.flush()
            projects = [Project(user_id=user.id, name=name) for name in ("a", "b")]
            db.add_all(projects)
            db.flush()
            self.uid, self.a, self.b = user.id, projects[0].id, projects[1].id
            db.add(UserSetting(user_id=self.uid, memory_enabled=True,
                memory_auto_candidate_enabled=True, knowledge_embedding_dimensions=128,
                knowledge_embedding_model="capacity-test-128"))
            conv = Conversation(user_id=self.uid, project_id=self.a, title="容量测试", model_name="synthetic")
            db.add(conv)
            db.flush()
            self.cid = conv.id
            memory = UserMemory(user_id=self.uid, title="旅行偏好", content="喜欢到雪山滑雪", project_id=self.a)
            db.add(memory)
            db.commit()
            self.mid = memory.id
        self.embedding = SimpleNamespace(embed_texts=AsyncMock(side_effect=lambda **kw:
            [[1.] + [0.] * 127 for _ in kw["texts"]]))
        self.reader = MemoryRetrievalService(session_factory=self.sessions, embedding_service=self.embedding,
                                            read_cache=self.cache)

    def tearDown(self):
        for key in set(self.keys):
            self.client.delete(key)

    def retrieve(self, query="冬季假期去哪", project=None):
        return asyncio.run(self.reader.retrieve(user_id=self.uid, project_id=project or self.a,
                                               query=query, max_chars=2000))

    def test_cache_hit_skips_embedding_but_keeps_pg_revalidation(self):
        first = self.retrieve()
        self.assertEqual(first.records[0]["id"], self.mid)
        second = self.retrieve()
        self.assertEqual(second.diagnostics["read_cache"], "hit")
        self.assertEqual(second.records, first.records)
        self.assertEqual(self.embedding.embed_texts.call_count, 1)

    def test_revoked_memory_cannot_return_after_late_old_cache_fill(self):
        self.retrieve()
        key = self.keys[-1]
        field = next(iter(self.client.data[key])) if isinstance(self.client, FakeRedis) else self.client.hkeys(key)[0]
        old_value = self.client.hget(key, field)
        with self.sessions() as db:
            repo = UserMemoryRepository(db)
            MemoryService(repo).revoke_memory(memory=repo.get_by_user(self.mid, self.uid))
        self.client.hset(key, field, old_value)
        self.assertEqual(self.retrieve().records, [])

    def test_cache_hit_rechecks_revocation_after_metadata_read(self):
        self.retrieve()
        original = self.cache.get
        def revoke(key, field):
            with self.sessions() as db:
                db.execute(update(UserMemory).where(UserMemory.id == self.mid).values(status="revoked", is_enabled=False))
                db.commit()
            return original(key, field)
        with patch.object(self.cache, "get", side_effect=revoke):
            self.assertEqual(self.retrieve().records, [])

    def test_expiry_scope_setting_and_model_changes_invalidate_cache(self):
        self.retrieve()
        with self.sessions() as db:
            db.get(UserMemory, self.mid).project_id = self.b
            db.commit()
        self.assertEqual(self.retrieve().records, [])
        with self.sessions() as db:
            row = db.get(UserMemory, self.mid)
            row.project_id, row.expires_at = self.a, datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()
        self.assertEqual(self.retrieve().records, [])
        with self.sessions() as db:
            db.get(UserMemory, self.mid).expires_at = None
            db.scalar(select(UserSetting).where(UserSetting.user_id == self.uid)).knowledge_embedding_model = "new-model"
            db.commit()
        self.assertNotEqual(self.retrieve().diagnostics["read_cache"], "hit")
        with self.sessions() as db:
            db.scalar(select(UserSetting).where(UserSetting.user_id == self.uid)).memory_enabled = False
            db.commit()
        self.assertEqual(self.retrieve().records, [])

    def test_project_and_user_isolation(self):
        self.retrieve()
        self.assertEqual(self.retrieve(project=self.b).records, [])
        with self.sessions() as db:
            other = User(username=f"other-{uuid4().hex[:8]}", email=f"{uuid4()}@example.test")
            db.add(other)
            db.commit()
            other_id = other.id
        result = asyncio.run(self.reader.retrieve(user_id=other_id, project_id=self.a,
            query="冬季假期去哪", max_chars=2000))
        self.assertFalse(result.records)

    def test_redis_error_and_malformed_data_fall_back_to_pg(self):
        first = self.retrieve()
        with patch.object(self.client, "hget", side_effect=RedisError("不可用")):
            result = self.retrieve()
        self.assertEqual(result.records, first.records)
        self.assertEqual(result.diagnostics["read_cache"], "unavailable")
        with patch.object(self.client, "hget", return_value=b'{"ids":null}'):
            self.assertEqual(self.retrieve().records, first.records)

    def test_cache_never_contains_memory_body_credentials_or_answer(self):
        self.retrieve()
        key = self.keys[-1]
        values = list(self.client.data[key].values()) if isinstance(self.client, FakeRedis) else self.client.hvals(key)
        value = values[0].decode() if isinstance(values[0], bytes) else values[0]
        self.assertNotIn("喜欢到雪山滑雪", value)
        self.assertNotIn("api_key", value)
        self.assertEqual(set(json.loads(value)), {"metadata", "ids", "hashes", "semantic", "ranking", "mode"})

    def test_cache_fields_are_bounded(self):
        for i in range(13):
            self.retrieve(query=f"冬季假期方案 {i}")
        self.assertLessEqual(self.client.hlen(self.keys[-1]), MemoryReadCache.MAX_QUERIES)

    def test_ranking_runs_outside_event_loop_thread(self):
        current = threading.get_ident()
        original = self.reader._rank
        def checked(*args):
            self.assertNotEqual(threading.get_ident(), current)
            return original(*args)
        with patch.object(self.reader, "_rank", side_effect=checked) as rank:
            self.retrieve()
        rank.assert_called_once()

    def test_cached_vector_fast_path_rejects_malformed_numeric_arrays(self):
        from app.services.memory_retrieval_service import text_hash, cosine
        import random
        from types import SimpleNamespace
        values = [random.Random(19+i).uniform(-1, 1) for i in range(128)]
        row = SimpleNamespace(id="vector", version=1, title="test", content="test",
            embedding_vector=json.dumps(values), embedding_signature="s")
        row.embedding_text_hash = text_hash(row)
        cached, missing = self.reader._cached_vectors([row], "s", 128)
        self.assertFalse(missing)
        semantic, _, _, _ = self.reader._rank([], [row], cached, values, "test", self.uid, self.a)
        self.assertAlmostEqual(semantic[row.id], cosine(values, values), places=12)
        for raw in (json.dumps([True] + [0.] * 127), json.dumps(["1"] + [0.] * 127),
                    json.dumps([float("nan")] + [0.] * 127), "[]", "[[1]]", "null", "[0,",
                    json.dumps([0.] * 128), json.dumps([1e308] * 128)):
            row.embedding_vector = raw
            cached, missing = self.reader._cached_vectors([row], "s", 128)
            self.assertFalse(cached)
            self.assertEqual(missing, [row])

    def history(self, count=1000, boundary_index=99):
        now = datetime.now(timezone.utc) - timedelta(hours=1)
        ids = [str(uuid4()) for _ in range(count)]
        with self.sessions() as db:
            db.execute(insert(Message), [dict(id=mid, conversation_id=self.cid, sequence=i+1,
                role="user" if i % 2 == 0 else "assistant", content=f"历史证据 {i}", status="done",
                created_at=now + timedelta(seconds=i)) for i, mid in enumerate(ids)])
            conv = db.get(Conversation, self.cid)
            conv.context_summary, conv.context_summary_boundary_message_id = "旧摘要", ids[boundary_index]
            aid = str(uuid4())
            db.add(Message(id=aid, conversation_id=self.cid, role="assistant", content="", status="streaming",
                           sequence=count+1, created_at=now + timedelta(seconds=count)))
            db.commit()
        return ids, aid

    def test_history_window_uses_oldest_backlog_and_latest_messages(self):
        ids, aid = self.history()
        with self.sessions() as db:
            repo = MessageRepository(db)
            rows = repo.list_for_context(db.get(Conversation, self.cid), exclude_message_id=aid)
            self.assertEqual([row.id for row in rows], [ids[99], *ids[100:148], *ids[-24:]])
            self.assertLessEqual(len(rows), 73)

    def test_regeneration_anchors_actual_user_after_many_background_answers(self):
        ids, aid = self.history()
        with self.sessions() as db:
            last = db.get(Message, aid)
            start = last.created_at
            db.execute(insert(Message), [dict(id=str(uuid4()), conversation_id=self.cid, sequence=1002+i,
                role="assistant", content="后台结果", status="done", created_at=start+timedelta(seconds=i+1))
                for i in range(30)])
            db.commit()
            final = db.scalar(select(Message).where(Message.conversation_id == self.cid)
                              .order_by(Message.sequence.desc()).limit(1))
            repo = MessageRepository(db)
            user = repo.latest_user_before(final)
            self.assertEqual(user.id, ids[998])
            rows = repo.list_for_context(db.get(Conversation, self.cid), through_message_id=user.id)
            self.assertEqual(rows[-1].id, user.id)
            frozen = repo.freeze_context_history(rows)
            db.commit()
            self.assertEqual(frozen.through_message_id, user.id)
            self.assertLessEqual(len(frozen), 73)

    def test_bounded_summary_never_jumps_over_unread_backlog(self):
        ids, aid = self.history()
        for mode in ("conservative", "balanced", "long-context"):
            with self.sessions() as db:
                conv = db.get(Conversation, self.cid)
                conv.context_summary_boundary_message_id = ids[99]
                db.commit()
                repo = MessageRepository(db)
                rows = repo.list_for_context(conv, exclude_message_id=aid)
                assembly = ChatContextAssemblyService(db=db, user_id=self.uid,
                    conversation_repo=ConversationRepository(db), message_service=MessageService(repo), tool_trace_repo=None)
                runtime = SimpleNamespace(governance_service=ContextGovernanceService(ContextBudgetPlanner.build(
                    model_context_window=16000, context_mode=mode)), resolved_model="synthetic",
                    settings=SimpleNamespace(), provider_type="openai-compatible", base_url="https://example.test", provider_api_key=None)
                with patch("app.services.chat_context_assembly_service.ChatProviderService.complete_chat",
                           new=AsyncMock(return_value="新摘要")):
                    result = asyncio.run(assembly._refresh_context_summary(conversation=conv, runtime=runtime,
                        expected_history=[(r.id, r.role, r.content, r.generation_id) for r in rows],
                        assistant_id=aid, generation_id=db.get(Message, aid).generation_id, bounded_history=True))
                self.assertEqual(result.boundary_message_id, ids[147])
                self.assertEqual(result.stats["summary_refresh_source_messages"], 48)
                db.commit()

    def test_attachments_are_eager_loaded_for_bounded_window(self):
        _, aid = self.history()
        statements = []
        def count(conn, cursor, statement, params, context, many):
            statements.append(statement)
        event.listen(self.engine, "before_cursor_execute", count)
        try:
            with self.sessions() as db:
                rows = MessageRepository(db).list_for_context(db.get(Conversation, self.cid), exclude_message_id=aid)
                for row in rows:
                    list(row.attachments)
            self.assertLessEqual(len(statements), 8)
        finally:
            event.remove(self.engine, "before_cursor_execute", count)

    def test_incremental_extraction_never_loads_full_history(self):
        ids, aid = self.history()
        with self.sessions() as db:
            db.get(Message, aid).status = "done"
            db.get(Conversation, self.cid).memory_extraction_cursor = 900
            db.commit()
            with patch.object(MessageRepository, "list_by_conversation", side_effect=AssertionError("禁止全历史")):
                job = MemoryExtractionJobService(db).enqueue_after_turn(user_id=self.uid,
                    conversation_id=self.cid, assistant_message_id=aid, force=True)
                self.assertEqual(job.source_message_ids.split(","), ids[900:948:2])
                worker = MemoryCandidateWorker(session_factory=self.sessions)
                self.assertEqual([row.id for row in worker._load_sources(db, job)], ids[900:948:2])


class ChatMemoryCapacityTest(CapacityCases, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(cls.engine)

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()


@unittest.skipUnless(os.getenv("TEST_POSTGRES_URL"), "需要隔离 PostgreSQL 和 Redis 服务")
class ChatMemoryCapacityPostgresTest(CapacityCases, unittest.TestCase):
    def test_sixteen_readers_share_cache_without_duplicate_embedding(self):
        self.retrieve()
        barrier = threading.Barrier(16)
        original = self.cache.get
        def synchronized(key, field):
            barrier.wait(timeout=15)
            return original(key, field)
        with patch.object(self.cache, "get", side_effect=synchronized):
            with ThreadPoolExecutor(max_workers=16) as pool:
                results = list(pool.map(lambda _: self.retrieve(), range(16)))
        self.assertTrue(all(result.diagnostics["read_cache"] == "hit" for result in results))
        self.assertTrue(all(result.records[0]["id"] == self.mid for result in results))
        self.assertEqual(self.embedding.embed_texts.call_count, 1)

    @classmethod
    def setUpClass(cls):
        url = make_url(os.environ["TEST_POSTGRES_URL"])
        cls.name = "aiws_capacity_test_" + uuid4().hex
        cls.admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
        with cls.admin.connect() as conn:
            conn.execute(text(f'create database "{cls.name}"'))
        cls.engine = create_engine(url.set(database=cls.name))
        with cls.engine.begin() as conn:
            conn.execute(text("create extension if not exists vector"))
        Base.metadata.create_all(cls.engine)
        cls.redis_client = Redis.from_url(settings.knowledge_redis_url)
        cls.redis_client.ping()

    @classmethod
    def tearDownClass(cls):
        cls.redis_client.close()
        cls.engine.dispose()
        with cls.admin.connect() as conn:
            conn.execute(text(f'drop database "{cls.name}" with (force)'))
        cls.admin.dispose()
