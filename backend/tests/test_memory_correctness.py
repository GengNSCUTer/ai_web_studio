from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.user import User
from app.models.project import Project
from app.models.conversation import Conversation
from app.models.user_memory import UserMemory
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.memory_repo import UserMemoryRepository
from app.schemas.memory import MemorySuggestion, UserMemoryCreate, UserMemoryUpdate
from app.services.memory_service import MemoryService
from app.services.memory_policy import contains_credential
from backend.tests.test_memory_context import FakeMemoryRepository


class MemoryClassificationTest(unittest.TestCase):
    def test_changed_language_is_conflict_even_with_high_text_similarity(self):
        existing = SimpleNamespace(id="old", memory_type="profile", title="默认回答语言", content="用户默认喜欢中文回答")
        suggestion = MemorySuggestion(memory_type="profile", title=existing.title, content="用户默认喜欢英文回答")
        result = MemoryService.enrich_suggestion_risks(suggestions=[suggestion], existing_memories=[existing])[0]
        self.assertEqual(result.risk_level, "conflict")
        self.assertIsNone(result.duplicate_memory_id)
        self.assertEqual(result.conflict_memory_id, "old")

    def test_number_and_negation_changes_are_not_duplicates(self):
        for old, new in (("用户每周工作3天", "用户每周工作5天"), ("用户喜欢远程工作", "用户不喜欢远程工作")):
            with self.subTest(new=new):
                existing = SimpleNamespace(id="old", memory_type="fact", title="工作安排", content=old)
                result = MemoryService.enrich_suggestion_risks(suggestions=[MemorySuggestion(
                    memory_type="fact", title=existing.title, content=new)], existing_memories=[existing])[0]
                self.assertEqual(result.risk_level, "conflict")
                self.assertIsNone(result.duplicate_memory_id)

    def test_same_language_different_wording_is_duplicate(self):
        existing = SimpleNamespace(id="old", memory_type="profile", title="回答语言", content="默认用中文回答")
        result = MemoryService.enrich_suggestion_risks(suggestions=[MemorySuggestion(
            memory_type="instruction", title="回复语言", content="以后回答使用中文")], existing_memories=[existing])[0]
        self.assertEqual(result.risk_level, "duplicate")

    def test_equal_values_of_unrelated_facts_are_not_duplicates(self):
        old = SimpleNamespace(id="old", memory_type="fact", title="工作城市", content="上海")
        result = MemoryService.enrich_suggestion_risks(suggestions=[MemorySuggestion(
            memory_type="fact", title="出生城市", content="上海")], existing_memories=[old])[0]
        self.assertEqual(result.risk_level, "safe")

    def test_extra_language_constraints_and_database_versions_not_discarded(self):
        for title, old, new in (("回答语言", "默认英文回答", "默认英文回答并限制在五句话"),
                                ("数据库", "数据库使用 PostgreSQL 16", "数据库使用 PostgreSQL 18")):
            existing = SimpleNamespace(id="old", memory_type="fact", title=title, content=old)
            result = MemoryService.enrich_suggestion_risks(suggestions=[MemorySuggestion(
                memory_type="fact", title=title, content=new)], existing_memories=[existing])[0]
            self.assertEqual(result.risk_level, "conflict")
            self.assertIsNone(result.duplicate_memory_id)

    def test_relation_direction_cannot_be_ignored_by_bag_of_words(self):
        existing = SimpleNamespace(id="old", memory_type="fact", title="reporting line", content="Alice reports to Bob")
        result = MemoryService.enrich_suggestion_risks(suggestions=[MemorySuggestion(
            memory_type="fact", title=existing.title, content="Bob reports to Alice")], existing_memories=[existing])[0]
        self.assertEqual(result.risk_level, "conflict")

    def test_other_project_and_historical_values_do_not_block_candidate(self):
        suggestion = MemorySuggestion(memory_type="fact", title="数据库", content="PostgreSQL", project_id="b")
        old = SimpleNamespace(id="old", memory_type="fact", title="数据库", content="PostgreSQL", project_id="a")
        self.assertEqual(MemoryService.enrich_suggestion_risks(suggestions=[suggestion], existing_memories=[old])[0].risk_level, "safe")
        old.project_id, old.status = "b", "superseded"
        self.assertEqual(MemoryService.enrich_suggestion_risks(suggestions=[suggestion], existing_memories=[old])[0].risk_level, "safe")

    def test_eight_preferences_cannot_starve_related_fact(self):
        preferences = [SimpleNamespace(memory_type="profile", title=f"偏好{i}", content=f"个人表达风格{i}") for i in range(8)]
        fact = SimpleNamespace(memory_type="fact", title="项目数据库", content="项目数据库使用 PostgreSQL")
        selection = MemoryService(FakeMemoryRepository([*preferences, fact])).select_memories_for_query("u", query="项目数据库是什么")
        self.assertIn(fact, selection.memories)
        self.assertEqual(selection.always_on_count, 2)
        self.assertEqual(selection.relevant_count, 1)

    def test_explicit_current_language_removes_saved_language_from_prompt_only(self):
        preference = SimpleNamespace(memory_type="profile", title="回答语言", content="默认英文回答")
        fact = SimpleNamespace(memory_type="fact", title="数据库", content="数据库使用 PostgreSQL")
        service = MemoryService(FakeMemoryRepository([preference, fact]))
        context, count, _ = service.build_memory_context("u", query="这次请用中文解释数据库", max_chars=1000)
        self.assertNotIn("默认英文", context)
        self.assertIn("PostgreSQL", context)
        self.assertEqual(count, 1)
        self.assertIn(preference, service.select_memories_for_query("u", query="解释数据库").memories)

    def test_long_preference_cannot_consume_fact_character_budget(self):
        profile = SimpleNamespace(memory_type="profile", title="表达风格", content="偏好" * 180)
        fact = SimpleNamespace(memory_type="fact", title="数据库", content="项目数据库使用 PostgreSQL")
        context, count, chars = MemoryService(FakeMemoryRepository([profile, fact])).build_memory_context("u", query="数据库", max_chars=500)
        self.assertIn("PostgreSQL", context)
        self.assertEqual(count, 1)
        self.assertLessEqual(chars, 500)

    def test_credential_candidates_are_dropped_and_legacy_secrets_are_not_injected(self):
        for value in ("密码：x", "密码是fakepass", "api_key=fake-example-key", "Bearer fake.example.token", "sk-fake-example-only"):
            with self.subTest(value=value):
                self.assertTrue(contains_credential("凭证", value))
                self.assertEqual(MemoryService.normalize_suggestions([{"title": "凭证", "content": value}], max_candidates=5), [])
                service = MemoryService(FakeMemoryRepository([SimpleNamespace(memory_type="profile", title="凭证", content=value)]))
                self.assertEqual(service.build_memory_context("u", query="偏好", max_chars=500)[1], 0)
                self.assertEqual(service.build_existing_memory_text("u"), "无")
        self.assertFalse(contains_credential("安全规范", "不要把密码保存到记忆里"))


class MemoryPersistenceCorrectnessTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.factory() as db:
            users = [User(username=f"mem{i}", email=f"mem{i}@example.test") for i in range(2)]
            db.add_all(users)
            db.flush()
            self.user_id, self.other_user = [user.id for user in users]
            projects = [Project(user_id=self.user_id, name=f"p{i}") for i in range(2)]
            db.add_all(projects)
            db.flush()
            self.projects = [project.id for project in projects]
            conv = Conversation(user_id=self.user_id, project_id=self.projects[0], title="来源", model_name="test")
            db.add(conv)
            db.commit()
            self.conversation_id = conv.id

    def tearDown(self):
        self.engine.dispose()

    def service(self, db):
        return MemoryService(UserMemoryRepository(db), ConversationRepository(db))

    def candidate(self, db, **kwargs):
        values = dict(user_id=self.user_id, memory_type="profile", title="回答语言", content="以后默认英文回答", status="pending", is_enabled=False, risk_level="safe")
        values.update(kwargs)
        row = UserMemory(**values)
        db.add(row)
        db.commit()
        return row

    def test_create_update_and_approve_block_credentials_independent_of_risk_label(self):
        with self.factory() as db:
            service = self.service(db)
            with self.assertRaisesRegex(ValueError, "密码、密钥"):
                service.create_memory(self.user_id, UserMemoryCreate(title="普通偏好", content="密码是fake-value"))
            response = service.create_memory(self.user_id, UserMemoryCreate(title="表达", content="保持简洁"))
            row = db.get(UserMemory, response.id)
            with self.assertRaisesRegex(ValueError, "密码、密钥"):
                service.update_memory(memory=row, payload=UserMemoryUpdate(content="api_key: fake-test-key"))
            db.refresh(row)
            self.assertEqual(row.content, "保持简洁")
            unsafe = self.candidate(db, content="password=x")
            with self.assertRaisesRegex(ValueError, "密码、密钥"):
                service.approve_candidate(memory=unsafe)
            db.refresh(unsafe)
            self.assertEqual(unsafe.status, "pending")

    def test_same_fact_replacement_is_atomic_and_repeated_approval_idempotent(self):
        with self.factory() as db:
            service = self.service(db)
            old = service.create_memory(self.user_id, UserMemoryCreate(memory_type="profile", title="回答语言", content="默认中文回答"))
            candidate = self.candidate(db, supersedes_memory_id=old.id)
            result = service.approve_candidate(memory=candidate)
            repeated = service.approve_candidate(memory=candidate)
            self.assertEqual(result.id, repeated.id)
            self.assertEqual(result.version, repeated.version)
            self.assertEqual(result.fact_key, "response_language")
            self.assertEqual(result.fact_value, "en")
            self.assertEqual(db.get(UserMemory, old.id).status, "superseded")
            self.assertEqual(len(UserMemoryRepository(db).list_by_user(self.user_id, enabled_only=True)), 1)

    def test_cross_project_and_unrelated_fact_replacement_rejected_without_partial_write(self):
        with self.factory() as db:
            service = self.service(db)
            old = service.create_memory(self.user_id, UserMemoryCreate(memory_type="profile", title="回答语言", content="默认中文回答", project_id=self.projects[0]))
            for kwargs in (dict(project_id=self.projects[1]), dict(project_id=self.projects[0], title="居住城市", content="深圳")):
                candidate = self.candidate(db, supersedes_memory_id=old.id, **kwargs)
                with self.assertRaises(ValueError):
                    service.approve_candidate(memory=candidate)
                db.refresh(candidate)
                self.assertEqual(candidate.status, "pending")
                self.assertEqual(db.get(UserMemory, old.id).status, "active")

    def test_cross_user_replacement_is_rejected(self):
        with self.factory() as db:
            old = self.service(db).create_memory(self.other_user, UserMemoryCreate(memory_type="profile", title="回答语言", content="中文"))
            candidate = self.candidate(db, supersedes_memory_id=old.id)
            with self.assertRaises(ValueError):
                self.service(db).approve_candidate(memory=candidate)
            self.assertEqual(db.get(UserMemory, old.id).status, "active")

    def test_active_content_edit_creates_history_instead_of_overwriting(self):
        with self.factory() as db:
            service = self.service(db)
            old = service.create_memory(self.user_id, UserMemoryCreate(memory_type="profile", title="回答语言", content="默认中文回答"))
            row = db.get(UserMemory, old.id)
            new = service.update_memory(memory=row, payload=UserMemoryUpdate(content="默认英文回答", expected_version=old.version))
            self.assertNotEqual(new.id, old.id)
            self.assertEqual(new.supersedes_memory_id, old.id)
            self.assertEqual(row.content, "默认中文回答")
            self.assertEqual(row.status, "superseded")
            self.assertEqual(new.fact_value, "en")
            with self.assertRaisesRegex(ValueError, "后继版本"):
                UserMemoryRepository(db).delete(row)

    def test_stale_metadata_update_is_rejected(self):
        with self.factory() as db:
            service = self.service(db)
            result = service.create_memory(self.user_id, UserMemoryCreate(title="表达", content="保持简洁"))
            row = db.get(UserMemory, result.id)
            service.update_memory(memory=row, payload=UserMemoryUpdate(is_enabled=False, expected_version=1))
            with self.assertRaisesRegex(ValueError, "版本已变化"):
                service.update_memory(memory=row, payload=UserMemoryUpdate(is_enabled=True, expected_version=1))
            db.refresh(row)
            self.assertFalse(row.is_enabled)

    def test_turn_only_is_rejected_and_temporary_requires_expiry(self):
        with self.factory() as db:
            service = self.service(db)
            with self.assertRaisesRegex(ValueError, "本轮要求"):
                service.create_memory(self.user_id, UserMemoryCreate(title="回答语言", content="这次用英文回答"))
            candidate = self.candidate(db, content="今天用英文回答")
            with self.assertRaisesRegex(ValueError, "expires_at"):
                service.approve_candidate(memory=candidate)
            approved = service.approve_candidate(memory=candidate, expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
            self.assertIsNotNone(approved.expires_at)

    def test_temporary_override_preserves_and_restores_durable_baseline(self):
        with self.factory() as db:
            service = self.service(db)
            old = service.create_memory(self.user_id, UserMemoryCreate(memory_type="profile", title="回答语言", content="默认中文回答"))
            temp = service.create_memory(self.user_id, UserMemoryCreate(memory_type="profile", title="回答语言", content="今天默认英文回答", expires_at=datetime.now(timezone.utc) + timedelta(hours=1)))
            self.assertEqual(service.select_memories_for_query(self.user_id, query="你好").memories[0].id, temp.id)
            db.get(UserMemory, temp.id).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()
            self.assertEqual(service.select_memories_for_query(self.user_id, query="你好").memories[0].id, old.id)
            self.assertEqual(db.get(UserMemory, old.id).status, "active")

    def test_temporary_candidate_cannot_supersede_long_term_baseline(self):
        with self.factory() as db:
            service = self.service(db)
            old = service.create_memory(self.user_id, UserMemoryCreate(memory_type="profile", title="回答语言", content="中文回答"))
            candidate = self.candidate(db, content="今天用英文回答", supersedes_memory_id=old.id)
            with self.assertRaisesRegex(ValueError, "短期记忆不能"):
                service.approve_candidate(memory=candidate, expires_at=datetime.now(timezone.utc) + timedelta(hours=1))
            db.refresh(candidate)
            self.assertIsNone(candidate.expires_at)
            self.assertEqual(db.get(UserMemory, old.id).status, "active")

    def test_generic_edit_cannot_replace_baseline_with_temporary_value(self):
        with self.factory() as db:
            service = self.service(db)
            old = service.create_memory(self.user_id, UserMemoryCreate(memory_type="profile", title="回答语言", content="中文回答"))
            with self.assertRaisesRegex(ValueError, "短期记忆不能"):
                service.update_memory(memory=db.get(UserMemory, old.id), payload=UserMemoryUpdate(
                    content="今天用英文回答", expires_at=datetime.now(timezone.utc) + timedelta(hours=1)))
            self.assertEqual(db.get(UserMemory, old.id).status, "active")

    def test_all_memory_types_are_scoped_by_project_and_source_ownership_checked(self):
        with self.factory() as db:
            service = self.service(db)
            for kind in ("profile", "instruction", "fact", "project"):
                result = service.create_memory(self.user_id, UserMemoryCreate(memory_type=kind, title=f"背景{kind}", content=f"项目测试 {kind}", source_conversation_id=self.conversation_id))
                self.assertEqual(result.project_id, self.projects[0])
            self.assertEqual(service.select_memories_for_query(self.user_id, query="项目测试", project_id=self.projects[1]).memories, [])
            self.assertTrue(service.select_memories_for_query(self.user_id, query="项目测试", project_id=self.projects[0]).memories)
            with self.assertRaisesRegex(ValueError, "来源会话"):
                service.create_memory(self.other_user, UserMemoryCreate(title="背景", content="项目测试", source_conversation_id=self.conversation_id))

    def test_conflict_and_duplicate_create_cannot_bypass_review(self):
        with self.factory() as db:
            service = self.service(db)
            service.create_memory(self.user_id, UserMemoryCreate(memory_type="profile", title="回答语言", content="中文回答"))
            for value in ("默认中文回答", "默认英文回答"):
                with self.assertRaises(ValueError):
                    service.create_memory(self.user_id, UserMemoryCreate(memory_type="profile", title="回答语言", content=value))


if __name__ == "__main__":
    unittest.main()
