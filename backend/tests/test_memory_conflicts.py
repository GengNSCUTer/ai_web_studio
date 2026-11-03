"""语义关系不是权限：验证召回、结构化判定及最终替换的版本约束。"""

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.models.user import User
from app.models.project import Project
from app.models.user_memory import UserMemory
from app.models.user_memory import MemoryExtractionJob
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user_setting import UserSetting
from app.repositories.memory_repo import UserMemoryRepository
from app.schemas.memory import MemorySuggestion, UserMemoryCreate, UserMemoryUpdate
from app.services.memory_conflict_service import MemoryConflictService
from app.services.memory_service import MemoryService
from app.services.memory_candidate_runtime import MemoryCandidateWorker, MemoryExtractionJobService

V = [1.] + [0.] * 127


class MemoryConflictTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        with self.sessions() as db:
            user, other = User(email='memory-conflict@example.test'), User(email='memory-other@example.test')
            db.add_all([user, other])
            db.flush()
            a, b = Project(user_id=user.id, name='a'), Project(user_id=user.id, name='b')
            db.add_all([a, b])
            db.flush()
            db.add(UserSetting(user_id=user.id, provider_type='openai-compatible', api_base_url='https://example.test/v1',
                knowledge_embedding_dimensions=128, knowledge_embedding_model='test-model'))
            db.commit()
            self.user, self.other, self.project, self.other_project = user.id, other.id, a.id, b.id
        self.provider = SimpleNamespace(complete_chat=AsyncMock())
        self.adapter = SimpleNamespace(embed_texts=AsyncMock(side_effect=lambda **kw: [V for _ in kw['texts']]))
        self.resolver = MemoryConflictService(session_factory=self.sessions, provider_service=self.provider, embedding_service=self.adapter)
        self.relation, self.confidence = 'contradiction', .95
        self.provider.complete_chat.side_effect = self.answer

    def tearDown(self):
        self.engine.dispose()

    async def answer(self, **kwargs):
        pairs = json.loads(kwargs['messages'][1]['content'])
        return json.dumps({'pairs': [dict(pair_id=p['pair_id'], relation=self.relation, confidence=self.confidence,
            impact='low', new_quote=p['new_content'][:100], old_quote=p['old_content'][:100], reason='逐字证据支持关系') for p in pairs]}, ensure_ascii=False)

    def old(self, **changes):
        values = dict(user_id=self.user, project_id=self.project, memory_type='project', title='数据存储',
                      content='项目数据库采用 PostgreSQL 16', status='active', is_enabled=True, version=1, importance=.5)
        values.update(changes)
        with self.sessions() as db:
            row = UserMemory(**values)
            db.add(row)
            db.commit()
            return row.id

    def assess(self, title='数据库选型', content='项目数据库采用 MySQL 8', project=None):
        suggestion = MemorySuggestion(memory_type='project', title=title, content=content, confidence='high')
        return asyncio.run(self.resolver.assess(user_id=self.user, project_id=project or self.project, suggestions=[suggestion]))[0]

    def candidate(self, assessed):
        with self.sessions() as db:
            row = UserMemory(user_id=self.user, project_id=self.project, memory_type=assessed.memory_type,
                title=assessed.title, content=assessed.content, status='pending', is_enabled=False, version=1,
                risk_level=assessed.risk_level, supersedes_memory_id=assessed.conflict_memory_id,
                conflict_review_json=json.dumps(assessed.conflict_review, ensure_ascii=False))
            db.add(row)
            db.commit()
            return row.id

    def approve(self, memory_id, **options):
        with self.sessions() as db:
            repo = UserMemoryRepository(db)
            return MemoryService(repo).approve_candidate(memory=repo.get_by_user(memory_id, self.user), **options)

    def test_different_titles_semantic_contradiction_requires_explicit_target_and_ack(self):
        old = self.old()
        assessed = self.assess()
        self.assertEqual(assessed.conflict_memory_id, old)
        self.assertEqual(assessed.conflict_review['pairs'][0]['via'], 'embedding')
        candidate = self.candidate(assessed)
        with self.assertRaises(ValueError):
            self.approve(candidate)
        with self.assertRaises(ValueError):
            self.approve(candidate, supersedes_memory_id=old, expected_target_version=1)
        with self.assertRaises(ValueError):
            self.approve(candidate, supersedes_memory_id=old, acknowledge_conflicts=True)
        result = self.approve(candidate, supersedes_memory_id=old, expected_target_version=1, acknowledge_conflicts=True)
        self.assertEqual(result.status, 'active')
        self.assertEqual(result.supersedes_memory_id, old)
        with self.sessions() as db:
            previous = db.get(UserMemory, old)
            self.assertEqual((previous.status, previous.version), ('superseded', 2))

    def test_same_title_complement_can_coexist_without_replacing(self):
        old = self.old(title='技术栈', content='项目后端使用 FastAPI')
        self.relation = 'complement'
        assessed = self.assess(title='技术栈', content='项目缓存使用 Redis')
        self.assertEqual(assessed.risk_level, 'safe')
        self.assertFalse(assessed.conflict_review['requires_review'])
        result = self.approve(self.candidate(assessed))
        with self.sessions() as db:
            self.assertEqual(db.get(UserMemory, old).status, 'active')
            self.assertEqual(result.status, 'active')

    def test_different_conditions_do_not_replace_and_high_impact_still_requires_ack(self):
        old = self.old(title='数据库', content='测试环境使用 SQLite')
        self.relation = 'different_scope'
        assessed = self.assess(title='数据库', content='生产环境使用 PostgreSQL')
        self.assertTrue(assessed.conflict_review['requires_review'])
        candidate = self.candidate(assessed)
        with self.assertRaises(ValueError):
            self.approve(candidate)
        self.approve(candidate, acknowledge_conflicts=True)
        with self.sessions() as db:
            self.assertEqual(db.get(UserMemory, old).status, 'active')

    def test_semantic_duplicate_does_not_create_second_active_fact(self):
        self.old(content='项目默认保存资料30天')
        self.relation = 'duplicate'
        assessed = self.assess(content='本项目文档默认保留30天')
        self.assertEqual(assessed.risk_level, 'duplicate')
        with self.assertRaisesRegex(ValueError, '重复'):
            self.approve(self.candidate(assessed), acknowledge_conflicts=True)

    def test_low_confidence_complement_needs_human_review_before_coexistence(self):
        old = self.old(title='技术栈', content='项目后端使用 FastAPI')
        self.relation, self.confidence = 'complement', .6
        assessed = self.assess(title='技术栈', content='项目缓存使用 Redis')
        self.assertTrue(assessed.conflict_review['requires_review'])
        self.assertEqual(assessed.conflict_review['status'], 'complete')
        candidate = self.candidate(assessed)
        with self.assertRaises(ValueError):
            self.approve(candidate)
        result = self.approve(candidate, acknowledge_conflicts=True)
        self.assertEqual(result.status, 'active')
        self.assertTrue(result.conflict_review['human_resolution']['acknowledged'])
        with self.sessions() as db:
            self.assertEqual(db.get(UserMemory, old).status, 'active')

    def test_scope_and_user_filter_before_network(self):
        self.old(user_id=self.other)
        self.old(project_id=self.other_project)
        assessed = self.assess()
        self.assertEqual(assessed.conflict_review['pairs'], [])
        self.adapter.embed_texts.assert_not_awaited()
        self.provider.complete_chat.assert_not_awaited()

    def test_structured_language_conflict_needs_no_embedding_or_llm(self):
        old = self.old(memory_type='profile', title='回答语言', content='默认中文回答')
        assessed = self.assess(title='回答语言', content='默认英文回答')
        self.assertEqual(assessed.conflict_memory_id, old)
        self.adapter.embed_texts.assert_not_awaited()
        self.provider.complete_chat.assert_not_awaited()

    def test_invalid_model_json_ids_quotes_and_boolean_confidence_stay_pending(self):
        self.old()
        for raw in ('not-json', '{"pairs":[]}', json.dumps({'pairs':[dict(pair_id='p0', relation='complement',
            confidence=True, impact='low', new_quote='无关', old_quote='无关', reason='bad')]})):
            self.provider.complete_chat.side_effect = None
            self.provider.complete_chat.return_value = raw
            assessed = self.assess()
            self.assertTrue(assessed.conflict_review['requires_review'])
            self.assertEqual(assessed.conflict_review['pairs'][0]['relation'], 'uncertain')

    def test_source_or_target_change_and_new_active_fact_reject_old_analysis(self):
        old = self.old()
        candidate = self.candidate(self.assess())
        with self.sessions() as db:
            row = db.get(UserMemory, old)
            row.version += 1
            db.commit()
        with self.assertRaisesRegex(ValueError, '版本已变化'):
            self.approve(candidate, supersedes_memory_id=old, expected_target_version=1, acknowledge_conflicts=True)

    def test_new_active_memory_after_analysis_requires_recheck(self):
        candidate = self.candidate(self.assess())
        self.old(title='新事实')
        with self.assertRaisesRegex(ValueError, '重新检查'):
            self.approve(candidate)

    def test_recheck_updates_version_and_old_candidate_version_is_rejected(self):
        old = self.old()
        candidate = self.candidate(self.assess())
        updated = asyncio.run(self.resolver.recheck(user_id=self.user, memory_id=candidate, expected_version=1))
        self.assertEqual(updated.version, 2)
        with self.assertRaises(ValueError):
            self.approve(candidate, expected_version=1, supersedes_memory_id=old, expected_target_version=1, acknowledge_conflicts=True)

    def test_sensitive_new_or_old_content_never_sent_to_models(self):
        self.old(sensitivity='sensitive', content='用户的内部敏感资料')
        assessed = self.assess(content='联系邮箱 alice@example.test')
        self.adapter.embed_texts.assert_not_awaited()
        self.provider.complete_chat.assert_not_awaited()
        self.assertTrue(assessed.conflict_review['requires_review'])

    def test_manual_create_conflict_is_pending_and_does_not_mutate_old(self):
        old = self.old()
        assessed = self.assess()
        with self.sessions() as db:
            result = MemoryService(UserMemoryRepository(db)).create_memory(self.user, UserMemoryCreate(
                project_id=self.project, memory_type=assessed.memory_type, title=assessed.title, content=assessed.content), assessment=assessed)
            self.assertEqual(result.status, 'pending')
            self.assertEqual(db.get(UserMemory, old).status, 'active')

    def test_embedding_failure_does_not_mean_no_conflict(self):
        self.old()
        self.adapter.embed_texts.side_effect = RuntimeError('do-not-leak-secret')
        assessed = self.assess()
        self.assertTrue(assessed.conflict_review['requires_review'])
        self.assertNotIn('do-not-leak-secret', json.dumps(assessed.conflict_review))

    def test_same_batch_cannot_automatically_activate_conflicting_different_titles(self):
        with self.sessions() as db:
            setting = db.scalar(select(UserSetting))
            setting.memory_enabled = setting.memory_auto_candidate_enabled = setting.memory_auto_activate_enabled = True
            conversation = Conversation(user_id=self.user, project_id=self.project, title='同批', model_name='test')
            db.add(conversation)
            db.flush()
            first = Message(conversation_id=conversation.id, role='user', sequence=1, content='请记住：项目采用 PostgreSQL 16。')
            second = Message(conversation_id=conversation.id, role='user', sequence=2, content='请记住：项目采用 MySQL 8。')
            answer = Message(conversation_id=conversation.id, role='assistant', sequence=3, content='收到', status='done')
            db.add_all([first, second, answer])
            db.commit()
            suggestions = [dict(memory_type='project', title=title, content=text, confidence='high',
                source_message_id=row.id, evidence_quote=text) for title, text, row in (
                ('数据存储', '项目采用 PostgreSQL 16。', first), ('数据库选型', '项目采用 MySQL 8。', second))]
            MemoryExtractionJobService(db).enqueue_after_turn(user_id=self.user, conversation_id=conversation.id,
                assistant_message_id=answer.id, force=True)
        extractor = SimpleNamespace(complete_chat=AsyncMock(return_value=json.dumps(suggestions, ensure_ascii=False)))
        worker = MemoryCandidateWorker(session_factory=self.sessions, provider_service=extractor, conflict_service=self.resolver)
        asyncio.run(worker.run_once())
        with self.sessions() as db:
            rows = list(db.scalars(select(UserMemory)))
            self.assertEqual(sum(row.status == 'active' for row in rows), 1)
            pending = next(row for row in rows if row.status == 'pending')
            self.assertEqual(json.loads(pending.conflict_review_json)['status'], 'stale')
            self.assertEqual(db.scalar(select(MemoryExtractionJob)).status, 'succeeded')

    def test_active_edit_uses_relation_review_and_does_not_bypass_other_fact(self):
        old = self.old()
        other = self.old(title='资料保留', content='资料保留30天')
        assessed = self.assess(content='资料保留7天')
        with self.sessions() as db:
            repo = UserMemoryRepository(db)
            row = repo.get_by_user(old, self.user)
            with self.assertRaises(ValueError):
                MemoryService(repo).update_memory(memory=row, payload=UserMemoryUpdate(content='资料保留7天'), assessment=assessed)
            self.assertEqual(db.get(UserMemory, old).content, '项目数据库采用 PostgreSQL 16')
            self.assertEqual(db.get(UserMemory, other).status, 'active')


if __name__ == '__main__':
    unittest.main()
