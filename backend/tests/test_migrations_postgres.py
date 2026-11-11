"""真实 PostgreSQL 检查空库升级、历史收编、失败回滚和并发额度。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import os
import unittest
from uuid import uuid4
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from alembic.migration import MigrationContext

from app.core.migrations import FIRST_REVISION, require_current_schema, upgrade_database, migration_config
from alembic import command
from app.services.resource_quota_service import ResourceQuotaService
from fastapi import HTTPException


@unittest.skipUnless(os.getenv("TEST_POSTGRES_URL"), "需要隔离 PostgreSQL 测试环境")
class MigrationPostgresTest(unittest.TestCase):
    def setUp(self):
        source = make_url(os.environ["TEST_POSTGRES_URL"])
        self.name = "aiws_migrations_test_" + uuid4().hex[:12]
        self.admin = create_engine(source.set(database="postgres"), isolation_level="AUTOCOMMIT")
        with self.admin.connect() as c:
            c.execute(text(f'create database "{self.name}"'))
        self.engine = create_engine(source.set(database=self.name))

    def tearDown(self):
        self.engine.dispose()
        with self.admin.connect() as c:
            c.execute(text(f'drop database "{self.name}" with (force)'))
        self.admin.dispose()

    def test_fresh_upgrade_and_repeat_preserves_rows(self):
        upgrade_database(self.engine)
        require_current_schema(self.engine)
        with self.engine.begin() as c:
            c.execute(text("insert into users(id,email) values ('kept','migration@example.com')"))
        upgrade_database(self.engine)
        with self.engine.connect() as c:
            self.assertEqual(c.execute(text("select id from users where id='kept'")).scalar(), "kept")
            self.assertTrue(inspect(c).has_table("resource_leases"))

    def test_citation_column_keeps_old_messages_unreviewed(self):
        upgrade_database(self.engine, target="20261028_01")
        with self.engine.begin() as c:
            c.execute(text("insert into users(id,email) values ('u','citation-migration@example.test')"))
            c.execute(text("insert into conversations(id,user_id,title,model_name,is_archived,is_pinned) values ('c','u','test','test',false,false)"))
            c.execute(text("insert into messages(id,conversation_id,generation_id,role,content,status) values ('m','c','g','assistant','旧答案','done')"))
        upgrade_database(self.engine)
        with self.engine.connect() as c:
            row = c.execute(text("select content,status,citation_audit,mcp_resources_json from messages where id='m'")).one()
            self.assertEqual(tuple(row), ("旧答案", "done", None, None))

    def test_memory_search_upgrade_converts_valid_json_and_keeps_business_fields(self):
        import hashlib
        upgrade_database(self.engine, target="20261105_01")
        digest = hashlib.sha256("1:数据库\n项目使用PostgreSQL".encode()).hexdigest()
        with self.engine.begin() as c:
            c.execute(text("insert into users(id,email) values ('u','memory-migration@example.test')"))
            c.execute(text("insert into user_memories(id,user_id,memory_type,title,content,source,status,is_enabled,version,"
                "embedding_vector,embedding_signature,embedding_text_hash) values "
                "('m','u','fact','数据库','项目使用PostgreSQL','manual','active',true,1,'[1,0]','test',:digest)"), {"digest": digest})
        upgrade_database(self.engine)
        with self.engine.connect() as c:
            row = c.execute(text("select title,content,version,status,search_dimensions,search_embedding::text "
                                 "from user_memories where id='m'")).one()
            self.assertEqual(tuple(row), ("数据库", "项目使用PostgreSQL", 1, "active", 2, "[1,0]"))
            self.assertTrue(c.execute(text("select search_vector @@ to_tsquery('simple','数据') "
                                          "from user_memories where id='m'")).scalar())

    def test_current_legacy_is_validated_and_adopted_without_data_loss(self):
        upgrade_database(self.engine, target=FIRST_REVISION)
        with self.engine.begin() as c:
            c.execute(text("drop table alembic_version"))
            c.execute(text("insert into users(id,email) values ('kept','legacy@example.com')"))
        with self.assertRaisesRegex(RuntimeError, "adopt-legacy"):
            upgrade_database(self.engine)
        upgrade_database(self.engine, adopt_legacy=True)
        require_current_schema(self.engine)
        with self.engine.connect() as c:
            self.assertEqual(c.execute(text("select count(*) from users where id='kept'")).scalar(), 1)

    def test_memory_trigger_migration_preserves_cursor_and_round_trips(self):
        upgrade_database(self.engine, target="20261108_03")
        with self.engine.begin() as c:
            c.execute(text("insert into users(id,email) values ('u','trigger-migration@example.test')"))
            c.execute(text("insert into conversations(id,user_id,title,model_name,is_archived,is_pinned,memory_extraction_cursor) "
                           "values ('c','u','test','test',false,false,17)"))
            c.execute(text("insert into memory_extraction_jobs(id,user_id,conversation_id,idempotency_key,source_message_ids,"
                           "cursor_end,status,attempts,max_attempts,lease_version,result_count) "
                           "values ('j','u','c','old','m',17,'succeeded',1,3,1,0)"))
        upgrade_database(self.engine)
        with self.engine.connect() as c:
            self.assertEqual(tuple(c.execute(text("select memory_scan_cursor,memory_extraction_cursor,memory_trigger_state "
                                                 "from conversations where id='c'")).one()), (17, 17, None))
            self.assertEqual(tuple(c.execute(text("select trigger_action,trigger_reason,priority from memory_extraction_jobs "
                                                 "where id='j'")).one()), ("extract_now", "legacy_batch", 0))
        with self.engine.begin() as c:
            config = migration_config()
            config.attributes["connection"] = c
            command.downgrade(config, "20261108_03")
        upgrade_database(self.engine)
        require_current_schema(self.engine)

    def test_memory_trigger_evaluation_migration_creates_decision_audit(self):
        upgrade_database(self.engine, target="20261111_01")
        with self.engine.begin() as c:
            c.execute(text("insert into users(id,email) values ('u','trigger-audit@example.test')"))
            c.execute(text("insert into conversations(id,user_id,title,model_name,is_archived,is_pinned) "
                           "values ('c','u','test','test',false,false)"))
            c.execute(text("insert into messages(id,conversation_id,generation_id,role,content,status) "
                           "values ('m','c','g','user','正文不应写入审计表','done')"))
            c.execute(text("insert into memory_extraction_jobs(id,user_id,conversation_id,idempotency_key,source_message_ids,"
                           "cursor_end,status,attempts,max_attempts,lease_version,result_count) "
                           "values ('j','u','c','audit','m',1,'pending',0,3,0,0)"))
        upgrade_database(self.engine)
        with self.engine.begin() as c:
            c.execute(text("insert into memory_trigger_decisions(id,user_id,conversation_id,message_id,source_digest,"
                           "policy_version,action,reason,priority,job_id,job_created,context_json,semantic_model_called) "
                           "values ('d','u','c','m',repeat('a',64),'rules-v2','buffer','batch_fallback',0,'j',true,"
                           ":context_json,false)"), {"context_json": '{"message_chars":12}'})
        with self.engine.connect() as c:
            row = c.execute(text("select action,job_id,job_created,context_json from memory_trigger_decisions where id='d'")).one()
            self.assertEqual(tuple(row), ("buffer", "j", True, '{"message_chars":12}'))
            self.assertTrue(inspect(c).has_table("memory_trigger_decisions"))

    def test_unknown_legacy_schema_is_rejected_without_version_stamp(self):
        with self.engine.begin() as c:
            c.execute(text("create table users(id text primary key)"))
        with self.assertRaises(RuntimeError):
            upgrade_database(self.engine, adopt_legacy=True)
        with self.engine.connect() as c:
            self.assertIsNone(MigrationContext.configure(c).get_current_revision())

    def test_memory_semantic_reservation_is_user_scoped_across_two_workers(self):
        """两个会话同时进入 buffer，真正的 PG 行锁保证用户级间隔只有一个获准。"""
        from app.models.user import User
        from app.models.conversation import Conversation
        from app.models.message import Message
        from app.models.user_setting import UserSetting
        from app.services.setting_service import SettingService
        from app.repositories.setting_repo import UserSettingRepository
        from app.services.memory_candidate_runtime import MemoryExtractionJobService, MemoryCandidateWorker
        from app.models.memory_trigger_decision import MemoryTriggerDecision
        upgrade_database(self.engine)
        sessions = sessionmaker(self.engine, expire_on_commit=False)
        jobs = []
        with sessions() as db:
            user = User(email="semantic-concurrency@example.test", username="semantic")
            db.add(user)
            db.flush()
            SettingService(UserSettingRepository(db)).get_or_create_user_settings(user.id)
            setting = db.query(UserSetting).filter_by(user_id=user.id).one()
            setting.memory_auto_candidate_enabled = True
            setting.memory_semantic_trigger_enabled = True
            db.commit()
            for index in range(2):
                conv = Conversation(user_id=user.id, model_name="test")
                db.add(conv)
                db.flush()
                for seq in range(4):
                    db.add(Message(conversation_id=conv.id, role="user", sequence=seq * 2 + 1,
                                   content=f"团队会议记录 {index}-{seq}"))
                assistant = Message(conversation_id=conv.id, role="assistant", sequence=8,
                                    content="收到", status="done")
                db.add(assistant)
                db.commit()
                jobs.append(MemoryExtractionJobService(db).enqueue_after_turn(user_id=user.id,
                    conversation_id=conv.id, assistant_message_id=assistant.id).id)
        workers = [MemoryCandidateWorker(session_factory=sessions, owner=f"reservation-{index}") for index in range(2)]
        claims = [worker._claim() for worker in workers]
        self.assertEqual({claim[0] for claim in claims}, set(jobs))
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(worker._reserve_semantic_call, *claim) for worker, claim in zip(workers, claims)]
            results = [future.result(timeout=20) for future in futures]
        self.assertEqual(sorted(results), [False, True])
        with sessions() as db:
            self.assertEqual(db.query(MemoryTriggerDecision).filter_by(semantic_model_called=True).count(), 4)

    def test_failed_constraint_rolls_back_schema_and_version(self):
        upgrade_database(self.engine, target=FIRST_REVISION)
        with self.engine.begin() as c:
            c.execute(text("insert into users(id,email) values ('u','rollback@example.com')"))
            c.execute(text("insert into conversations(id,user_id,title,model_name,is_archived,is_pinned) values ('c','u','test','test',false,false)"))
            c.execute(text("create table resource_leases(id varchar(36) primary key)"))
        with self.assertRaises(Exception):
            upgrade_database(self.engine)
        with self.engine.connect() as c:
            self.assertEqual(MigrationContext.configure(c).get_current_revision(), FIRST_REVISION)
            self.assertTrue(inspect(c).has_table("resource_leases"))
            self.assertEqual(c.execute(text("select count(*) from conversations where id='c'")).scalar(), 1)

    def test_competing_process_sessions_cannot_exceed_limit(self):
        upgrade_database(self.engine)
        service = ResourceQuotaService(sessionmaker(self.engine))
        def acquire(_):
            try:
                return service.acquire("user", "chat", 2)
            except HTTPException as exc:
                return exc.status_code
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(acquire, range(8)))
        self.assertEqual(sum(isinstance(result, str) for result in results), 2)
        self.assertEqual(results.count(429), 6)
