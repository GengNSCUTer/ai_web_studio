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

from app.core.migrations import FIRST_REVISION, require_current_schema, upgrade_database
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

    def test_unknown_legacy_schema_is_rejected_without_version_stamp(self):
        with self.engine.begin() as c:
            c.execute(text("create table users(id text primary key)"))
        with self.assertRaises(RuntimeError):
            upgrade_database(self.engine, adopt_legacy=True)
        with self.engine.connect() as c:
            self.assertIsNone(MigrationContext.configure(c).get_current_revision())

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
