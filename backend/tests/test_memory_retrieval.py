"""验证混合召回的权限边界、缓存失效、降级和预算后诊断。"""

import asyncio
import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.conversation import Conversation
from app.models.project import Project
from app.models.user import User
from app.models.user_memory import UserMemory
from app.models.user_setting import UserSetting
from app.repositories.memory_repo import UserMemoryRepository
from app.repositories.setting_repo import UserSettingRepository
from app.schemas.memory import UserMemoryUpdate
from app.services.context_governance_service import ContextBudgetConfig, ContextGovernanceService
from app.services.memory_retrieval_service import MemoryRetrievalService, MemoryRetrievalResult, cosine
from app.services.memory_service import MemoryService
from app.services.prompt_builder_service import ContextPromptBuilder
from app.services.setting_service import SettingService

V = [1.0] + [0.0] * 127
ORTHOGONAL = [0.0, 1.0] + [0.0] * 126


class MemoryRetrievalTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        with self.sessions() as db:
            user = User(username="recall", email="recall@example.test")
            other = User(username="other", email="other@example.test")
            db.add_all([user, other])
            db.flush()
            projects = [Project(user_id=user.id, name=name) for name in ("a", "b")]
            db.add_all(projects)
            db.commit()
            self.user, self.other = user.id, other.id
            self.a, self.b = [p.id for p in projects]
            SettingService(UserSettingRepository(db)).get_or_create_user_settings(self.user)
            setting = db.scalar(select(UserSetting))
            setting.knowledge_embedding_model = "memory-test-model"
            setting.knowledge_embedding_dimensions = 128
            db.commit()
        self.adapter = SimpleNamespace(embed_texts=AsyncMock(return_value=[V, V]))
        self.service = MemoryRetrievalService(session_factory=self.sessions, embedding_service=self.adapter, timeout_seconds=.1)

    def tearDown(self):
        self.engine.dispose()

    def memory(self, **changes):
        values = dict(user_id=self.user, title="旅行", content="我喜欢去雪山度假", memory_type="fact", project_id=self.a)
        values.update(changes)
        with self.sessions() as db:
            row = UserMemory(**values)
            db.add(row)
            db.commit()
            return row.id

    def retrieve(self, query="下一次假期安排什么户外活动", project=None):
        return asyncio.run(self.service.retrieve(user_id=self.user, project_id=project or self.a, query=query, max_chars=1000))

    def test_semantic_matches_without_shared_words_and_reuses_cached_document(self):
        memory = self.memory()
        result = self.retrieve("冬季滑雪目的地")
        self.assertEqual(result.records[0]["id"], memory)
        self.assertEqual(result.records[0]["reason"], "semantic")
        self.adapter.embed_texts.return_value = [V]
        again = self.retrieve("冬季滑雪目的地")
        self.assertEqual(again.diagnostics["embedding_cache_hits"], 1)
        self.assertEqual(len(self.adapter.embed_texts.call_args.kwargs["texts"]), 1)

    def test_filters_other_users_projects_states_expiry_before_external_api(self):
        for changes in ({"user_id": self.other}, {"project_id": self.b}, {"status": "pending"},
                        {"status": "revoked"}, {"is_enabled": False},
                        {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)},
                        {"sensitivity": "sensitive"}, {"content": "api_key: secret-test-value"}):
            self.memory(**changes)
        self.memory()
        result = self.retrieve()
        self.assertEqual(result.diagnostics["eligible_count"], 1)
        self.assertEqual(len(self.adapter.embed_texts.call_args.kwargs["texts"]), 2)

    def test_legacy_source_project_is_filtered_before_embedding(self):
        with self.sessions() as db:
            conv = Conversation(user_id=self.user, project_id=self.b, title="legacy", model_name="test")
            db.add(conv)
            db.commit()
            conv_id = conv.id
        self.memory(project_id=None, source_conversation_id=conv_id)
        self.assertEqual(self.retrieve().records, [])
        self.adapter.embed_texts.assert_not_called()

    def test_network_failure_falls_back_without_exposing_exception(self):
        self.memory(title="数据库", content="项目采用 PostgreSQL 16")
        self.adapter.embed_texts.side_effect = RuntimeError("api_key=do-not-expose")
        result = self.retrieve("项目数据库版本")
        self.assertEqual(result.diagnostics["mode"], "lexical")
        self.assertEqual(result.records[0]["reason"], "lexical")
        self.assertNotIn("do-not-expose", str(result.diagnostics))

    def test_timeout_is_bounded_and_keeps_lexical_recall(self):
        self.memory(title="数据库", content="项目采用 PostgreSQL 16")
        async def delayed(**kwargs):
            await asyncio.sleep(.2)
        self.adapter.embed_texts.side_effect = delayed
        result = self.retrieve("数据库")
        self.assertEqual(result.diagnostics["fallback_reason"], "embedding_timeout")
        self.assertTrue(result.context_text)

    def test_invalid_vectors_are_rejected(self):
        self.memory(title="数据库", content="项目采用 PostgreSQL 16")
        for vectors in ([V], [V, [0] * 128], [V, [float("nan")] + [0] * 127], [V, [1, 0, 0]],
                        [V, [1e308] * 128], [V, [10 ** 400] + [0] * 127]):
            self.adapter.embed_texts.return_value = vectors
            self.assertEqual(self.retrieve("数据库").diagnostics["mode"], "lexical")

    def test_cosine_remains_finite_for_extreme_nonzero_vectors(self):
        for scale in (1e200, 1e-200):
            vector = [scale] + [0.0] * 127
            self.assertEqual(cosine(vector, vector), 1.0)
            self.assertEqual(cosine(vector, ORTHOGONAL), 0.0)

    def test_model_change_same_dimension_requires_new_document_vector(self):
        self.memory()
        self.retrieve()
        with self.sessions() as db:
            db.scalar(select(UserSetting)).knowledge_embedding_model = "another-model"
            db.commit()
        result = self.retrieve()
        self.assertEqual(result.diagnostics["embedding_cache_hits"], 0)
        self.assertEqual(result.diagnostics["embedding_generated"], 1)

    def test_model_settings_change_during_network_discards_semantic_result(self):
        self.memory()
        async def change(**kwargs):
            with self.sessions() as db:
                db.scalar(select(UserSetting)).knowledge_embedding_model = "changed-model"
                db.commit()
            return [V, V]
        self.adapter.embed_texts.side_effect = change
        result = self.retrieve("冬季滑雪目的地")
        self.assertFalse(result.records)
        self.assertEqual(result.diagnostics["fallback_reason"], "embedding_settings_changed")

    def test_other_user_source_cannot_masquerade_as_global_memory(self):
        with self.sessions() as db:
            conv = Conversation(user_id=self.other, title="foreign", model_name="test")
            db.add(conv)
            db.commit()
            source = conv.id
        self.memory(project_id=None, source_conversation_id=source)
        self.assertFalse(self.retrieve().records)
        self.adapter.embed_texts.assert_not_called()

    def test_private_identifiers_and_query_credentials_are_redacted_before_embedding(self):
        self.memory(content="联系人地址为 someone@example.test，喜欢滑雪")
        self.retrieve("password: do-not-send。冬季滑雪安排")
        texts = self.adapter.embed_texts.call_args.kwargs["texts"]
        self.assertNotIn("do-not-send", str(texts))
        self.assertNotIn("someone@example.test", str(texts))

    def test_revocation_during_embedding_is_not_injected_or_cached(self):
        memory = self.memory()
        async def revoke(**kwargs):
            with self.sessions() as db:
                repo = UserMemoryRepository(db)
                MemoryService(repo).revoke_memory(memory=repo.get_by_user(memory, self.user))
            return [V, V]
        self.adapter.embed_texts.side_effect = revoke
        self.assertEqual(self.retrieve().records, [])
        with self.sessions() as db:
            self.assertIsNone(db.get(UserMemory, memory).embedding_vector)

    def test_correction_during_embedding_discards_old_semantic_match(self):
        memory = self.memory()
        async def correct(**kwargs):
            with self.sessions() as db:
                repo = UserMemoryRepository(db)
                MemoryService(repo).update_memory(memory=repo.get_by_user(memory, self.user),
                    payload=UserMemoryUpdate(content="我喜欢看室内话剧"))
            return [V, V]
        self.adapter.embed_texts.side_effect = correct
        self.assertEqual(self.retrieve("冬季滑雪目的地").records, [])

    def test_disabling_memory_during_embedding_stops_injection(self):
        self.memory()
        async def disable(**kwargs):
            with self.sessions() as db:
                db.scalar(select(UserSetting)).memory_enabled = False
                db.commit()
            return [V, V]
        self.adapter.embed_texts.side_effect = disable
        result = self.retrieve()
        self.assertEqual(result.records, [])
        self.assertEqual(result.diagnostics["mode"], "disabled")
        self.assertNotIn("snapshot_hashes", result.diagnostics)

    def test_current_turn_language_request_does_not_mutate_default(self):
        memory = self.memory(memory_type="profile", title="回答语言", content="默认英文回答")
        self.assertFalse(self.retrieve("这次用中文回答").records)
        self.assertEqual(self.retrieve("解释 TCP").records[0]["id"], memory)
        self.adapter.embed_texts.assert_not_called()

    def test_low_similarity_irrelevant_memory_is_not_forced_into_prompt(self):
        self.memory()
        self.adapter.embed_texts.return_value = [V, ORTHOGONAL]
        self.assertFalse(self.retrieve("PostgreSQL索引").records)

    def test_ttl_expiring_during_embedding_is_not_injected(self):
        memory = self.memory()
        async def expire(**kwargs):
            with self.sessions() as db:
                db.get(UserMemory, memory).expires_at = datetime.now(timezone.utc)-timedelta(seconds=1)
                db.commit()
            return [V, V]
        self.adapter.embed_texts.side_effect = expire
        self.assertFalse(self.retrieve("冬季滑雪目的地").records)

    def test_dual_route_result_ranks_above_single_route(self):
        dual = self.memory(title="数据库", content="数据库使用 PostgreSQL 16")
        self.memory(title="户外活动", content="喜欢雪山度假")
        async def embed(**kwargs):
            return [V, *[V if "数据库" in text else [.9, .43589]+[0]*126 for text in kwargs["texts"][1:]]]
        self.adapter.embed_texts.side_effect = embed
        result = self.retrieve("数据库")
        self.assertEqual(result.records[0]["id"], dual)
        self.assertEqual(result.records[0]["reason"], "hybrid")

    def test_embedding_cache_does_not_change_memory_recency_or_version(self):
        memory = self.memory()
        with self.sessions() as db:
            row = db.get(UserMemory, memory)
            original = row.updated_at, row.version
        self.retrieve()
        with self.sessions() as db:
            row = db.get(UserMemory, memory)
            self.assertEqual((row.updated_at, row.version), original)

    def test_selected_and_injected_counts_follow_total_budget(self):
        self.memory()
        result = self.retrieve()
        self.assertEqual(result.after_governance("")["injected_count"], 0)
        self.assertEqual(result.after_governance(result.context_text)["injected_count"], 1)
        self.assertNotIn("line", result.after_governance(result.context_text)["injected"][0])

    def test_cold_index_batch_is_bounded(self):
        for i in range(35):
            self.memory(title=f"档案{i}", content=f"不同事项{i}")
        async def embed(**kwargs):
            return [V for _ in kwargs["texts"]]
        self.adapter.embed_texts.side_effect = embed
        result = self.retrieve()
        self.assertEqual(result.diagnostics["embedding_generated"], 32)
        self.assertEqual(result.diagnostics["embedding_deferred"], 3)

    def test_governance_retains_only_whole_memory_lines(self):
        text = "记忆参考：\n- [事实] 小事实: 完整内容\n- [事实] 大事实: " + "长" * 400
        prompt = ContextPromptBuilder().build_chat_messages(messages=[SimpleNamespace(id="u", role="user", content="问题", attachments=[])],
            system_prompt=None, memory_context=text, context_summary=None, summary_boundary_message_id=None,
            external_context=None, attachment_context=None, provider_type="openai-compatible")
        budget = ContextBudgetConfig(model_context_window=8192, context_mode="balanced", reserved_output_tokens=100,
            max_history_messages=10, max_total_tokens=9000, max_attachment_tokens=1000, max_image_equiv_tokens=1000,
            max_summary_tokens=1000, max_total_chars=len(prompt.messages[0]["content"])+350,
            max_attachment_chars=1000, max_image_equiv_chars=1000, max_summary_chars=1000)
        governed = ContextGovernanceService(budget=budget).govern_messages(prompt.messages)
        retained = governed.retained_reference_texts.get("long_term_memory", "")
        self.assertNotIn("大事实", retained)
        self.assertIn("完整内容", retained)


if __name__ == "__main__":
    unittest.main()
