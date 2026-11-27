"""摘要恢复的业务反例：安全回退、长消息、已提交进度和过期Worker。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import os
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from sqlalchemy import delete, select

from backend.tests import test_chat_memory_capacity as capacity_fixtures
from backend.tests import test_context_summary_lifecycle as lifecycle_fixtures
from app.models.conversation import Conversation
from app.models.conversation_summary import ConversationSummaryCheckpoint as Checkpoint, ConversationSummaryJob as Job
from app.models.message import Message
from app.repositories.message_repo import MessageRepository
from app.services.context_governance_service import ContextBudgetPlanner
from app.services.conversation_summary_service import ConversationSummaryService
from app.services.message_service import MessageService
from app.services.summary_rebuild_worker import SummaryRebuildWorker
from app.services.summary_output import SUMMARY_SECTIONS
from app.services.tokenizer_service import TokenizerEstimator
from app.services.runtime_lease import utcnow


class SummaryRebuildCases:
    def setUp(self):
        self.fixture = capacity_fixtures.ChatMemoryCapacityTest('test_history_window_uses_oldest_backlog_and_latest_messages')
        self.fixture.engine = self.engine
        self.fixture.setUp()
        self.sessions, self.cid, self.uid = self.fixture.sessions, self.fixture.cid, self.fixture.uid
        self.calls = []
        self.runtime = SimpleNamespace(provider_type='openai-compatible', base_url='https://example.test',
            provider_api_key=None, resolved_model='test-summary', tokenizer=TokenizerEstimator(),
            budget=ContextBudgetPlanner.build(model_context_window=16000, context_mode='balanced'))
        test = self
        class Provider:
            async def complete_chat(self, **kwargs):
                test.calls.append(kwargs['messages'])
                return '\n'.join(f'## {title}\n保留有效历史的测试摘要' for title in SUMMARY_SECTIONS)
        self.worker = SummaryRebuildWorker(session_factory=self.sessions, provider_factory=Provider)
        self.config = patch('app.services.chat_execution_service.ChatExecutionService._build_runtime_config', return_value=self.runtime)
        self.config.start()

    def tearDown(self):
        self.config.stop()
        with self.sessions() as db:
            db.execute(delete(Job).where(Job.conversation_id == self.cid))
            db.commit()
        self.fixture.tearDown()

    def history(self, count=160, boundary=79):
        self.ids, self.aid = self.fixture.history(count=count, boundary_index=boundary)
        return self.ids

    def enqueue(self):
        with self.sessions() as db:
            conv = MessageRepository(db).lock_conversation(self.cid)
            row = ConversationSummaryService(db).ensure_job(conv)
            db.commit()
            return row.id

    def checkpoint(self, at):
        with self.sessions() as db:
            conv = MessageRepository(db).lock_conversation(self.cid)
            conv.context_summary, conv.context_summary_boundary_message_id = f'覆盖到{at}', self.ids[at-1]
            ConversationSummaryService(db).save_checkpoint(conv)
            db.commit()

    def test_roll_back_to_unaffected_checkpoint_and_rebuild(self):
        self.history(1000, 899)
        for at in (200,400,600,800,900):
            self.checkpoint(at)
        with self.sessions() as db:
            MessageService(MessageRepository(db)).delete_message(self.ids[749], self.cid)
            conv = db.get(Conversation, self.cid)
            self.assertEqual(conv.context_summary_boundary_message_id, self.ids[599])
            self.assertEqual(db.scalars(select(Checkpoint.boundary_message_id).where(Checkpoint.conversation_id==self.cid)).all(), self.ids[199:600:200])
        for _ in range(12):
            if not asyncio.run(self.worker.run_once()): break
        with self.sessions() as db:
            job = db.scalar(select(Job).where(Job.conversation_id==self.cid))
            self.assertEqual(job.status, 'succeeded')
            self.assertEqual(job.cursor_message_id, self.ids[975])
        self.assertLessEqual(len(self.calls), 8)

    def test_delete_early_no_fake_checkpoint_and_no_deleted_input(self):
        self.history(180, 129)
        with self.sessions() as db:
            MessageService(MessageRepository(db)).delete_message(self.ids[0], self.cid)
            self.assertIsNone(db.get(Conversation,self.cid).context_summary)
        for _ in range(10):
            if not asyncio.run(self.worker.run_once()): break
        joined = '\n'.join(call[-1]['content'] for call in self.calls)
        self.assertNotIn('历史证据 0\n', joined)
        self.assertIn('历史证据 155', joined)

    def test_boundary_checkpoint_is_not_reused(self):
        self.history()
        self.checkpoint(40)
        self.checkpoint(80)
        with self.sessions() as db:
            MessageService(MessageRepository(db)).delete_message(self.ids[79],self.cid)
            self.assertEqual(db.get(Conversation,self.cid).context_summary_boundary_message_id,self.ids[39])

    def test_transaction_rollback_preserves_message_summary_and_checkpoints(self):
        self.history()
        self.checkpoint(80)
        with self.sessions() as db:
            repo = MessageRepository(db)
            with patch.object(repo, 'delete', side_effect=RuntimeError('故障')):
                with self.assertRaises(RuntimeError): MessageService(repo).delete_message(self.ids[0],self.cid)
            self.assertIsNotNone(db.get(Message,self.ids[0]))
            self.assertEqual(db.get(Conversation,self.cid).context_summary,'覆盖到80')
            self.assertEqual(len(list(db.scalars(select(Job).where(Job.conversation_id==self.cid)))),0)

    def test_recent_mutation_does_not_restart_active_repair(self):
        self.history()
        jid = self.enqueue()
        claim = self.worker.claim()
        with self.sessions() as db:
            version = db.get(Job,jid).lease_version
            MessageService(MessageRepository(db)).delete_message(self.ids[-1],self.cid)
            self.assertEqual(db.get(Job,jid).status,'running')
            self.assertEqual(db.get(Job,jid).lease_version,version)
        self.assertTrue(self.worker.heartbeat(claim))

    def test_lease_takeover_rejects_old_commit_even_with_cached_object(self):
        self.history()
        jid = self.enqueue()
        old = self.worker.claim()
        data = self.worker.load(old)
        with self.sessions() as db:
            db.get(Job,jid).lease_expires_at=utcnow()-timedelta(seconds=1)
            db.commit()
        other = SummaryRebuildWorker(session_factory=self.sessions, owner='new-worker')
        new = other.claim()
        self.assertGreater(new.version,old.version)
        self.assertFalse(self.worker.complete(old,data,'迟到摘要'))
        self.assertFalse(self.worker.heartbeat(old))

    def test_edit_during_provider_wait_cannot_restore_old_history(self):
        self.history()
        self.enqueue()
        claim = self.worker.claim()
        data = self.worker.load(claim)
        with self.sessions() as db:
            repo = MessageRepository(db)
            conv = repo.lock_conversation(self.cid)
            repo.invalidate_summary_for_changes(conv,[self.ids[90]])
            db.get(Message,self.ids[90]).content='新内容'
            db.commit()
        self.assertFalse(self.worker.complete(claim,data,'旧内容的摘要'))

    def test_committed_batch_survives_new_worker_and_duplicate_claim(self):
        self.history()
        jid = self.enqueue()
        self.assertTrue(asyncio.run(self.worker.run_once()))
        with self.sessions() as db:
            cursor = db.get(Job,jid).cursor_message_id
        fresh = SummaryRebuildWorker(session_factory=self.sessions, owner='replacement')
        claim = fresh.claim()
        self.assertIsNone(self.worker.claim())
        data = fresh.load(claim)
        self.assertEqual(data.old_boundary,cursor)
        self.assertGreater(self.ids.index(data.batch.rows[0].id),self.ids.index(cursor))

    def test_transient_retry_is_finite_and_progress_is_not_lost(self):
        self.history()
        jid = self.enqueue()
        for attempt in range(3):
            claim = self.worker.claim()
            self.worker.fail(claim,transient=True)
            with self.sessions() as db:
                job=db.get(Job,jid)
                self.assertEqual(job.attempts,attempt+1)
                job.next_attempt_at=utcnow()-timedelta(seconds=1)
                db.commit()
        self.assertIsNone(self.worker.claim())
        with self.sessions() as db:
            self.assertEqual(db.get(Job,jid).status,'failed')
            conv=MessageRepository(db).lock_conversation(self.cid)
            self.assertEqual(ConversationSummaryService(db).ensure_job(conv,retry_failed=True).id,jid)
            db.commit()
        self.assertIsNotNone(self.worker.claim())

    def test_checkpoint_retention_is_bounded(self):
        self.history()
        for at in range(1,40): self.checkpoint(at)
        with self.sessions() as db:
            self.assertEqual(len(list(db.scalars(select(Checkpoint).where(Checkpoint.conversation_id==self.cid)))),32)

    def test_long_message_tail_is_consumed_before_boundary_advances(self):
        self.history(30,0)
        with self.sessions() as db:
            conv=db.get(Conversation,self.cid)
            conv.context_summary=conv.context_summary_boundary_message_id=None
            db.get(Message,self.ids[0]).content='超长内容。'*4000+'末尾验收事实：鲸鱼编号731。'
            db.commit()
        jid=self.enqueue()
        self.assertTrue(asyncio.run(self.worker.run_once()))
        with self.sessions() as db:
            self.assertEqual(db.get(Job,jid).partial_message_id,self.ids[0])
            self.assertIsNone(db.get(Conversation,self.cid).context_summary_boundary_message_id)
        for _ in range(30):
            if not asyncio.run(self.worker.run_once()): break
        self.assertIn('鲸鱼编号731', '\n'.join(call[-1]['content'] for call in self.calls))
        with self.sessions() as db:
            self.assertEqual(db.get(Job,jid).status,'succeeded')

    def test_no_transaction_during_model_wait_and_no_fallback_completion(self):
        self.history()
        jid=self.enqueue()
        engine=self.engine
        checked_out=[]
        class BadProvider:
            async def complete_chat(self, **kwargs):
                if hasattr(engine.pool, 'checkedout'):
                    checked_out.append(engine.pool.checkedout())
                raise ValueError('受控空摘要错误')
        worker=SummaryRebuildWorker(session_factory=self.sessions,provider_factory=BadProvider)
        asyncio.run(worker.run_once())
        self.assertTrue(all(count == 0 for count in checked_out), '模型等待时仍占数据库连接')
        with self.sessions() as db:
            self.assertEqual(db.get(Job,jid).status,'failed')
            self.assertEqual(db.get(Conversation,self.cid).context_summary_boundary_message_id,self.ids[79])

    def test_ten_thousand_messages_progress_without_skips(self):
        self.history(10000,0)
        with self.sessions() as db:
            conv=db.get(Conversation,self.cid)
            conv.context_summary=conv.context_summary_boundary_message_id=None
            db.commit()
        jid=self.enqueue()
        consumed=[]
        for _ in range(220):
            claim=self.worker.claim()
            if not claim: break
            data=self.worker.load(claim)
            consumed.extend(row.id for row in data.batch.rows)
            self.assertTrue(self.worker.complete(claim,data,'累积摘要'))
            self.assertFalse(self.worker.complete(claim,data,'重复提交不能覆盖'))
        self.assertEqual(consumed,self.ids[:9976])
        with self.sessions() as db:
            self.assertEqual(db.get(Job,jid).status,'succeeded')

    def test_stop_requeues_without_importing_half_summary(self):
        self.history()
        jid=self.enqueue()
        entered=asyncio.Event()
        class WaitingProvider:
            async def complete_chat(self,**kwargs):
                entered.set()
                await asyncio.Event().wait()
        worker=SummaryRebuildWorker(session_factory=self.sessions,provider_factory=WaitingProvider)
        async def scenario():
            task=asyncio.create_task(worker.run_once())
            await asyncio.wait_for(entered.wait(),5)
            worker.request_stop()
            await task
        asyncio.run(scenario())
        with self.sessions() as db:
            self.assertEqual(db.get(Job,jid).status,'queued')
            self.assertEqual(db.get(Job,jid).attempts,0)
            self.assertEqual(db.get(Conversation,self.cid).context_summary_boundary_message_id,self.ids[79])

    def test_status_and_retry_api_are_user_scoped(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.deps import get_db,get_current_user
        from app.api.routes.conversations import router
        self.history()
        app=FastAPI()
        app.include_router(router,prefix='/api')
        def database():
            with self.sessions() as db: yield db
        app.dependency_overrides[get_db]=database
        app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(id=self.uid)
        with TestClient(app) as client:
            path=f'/api/conversations/{self.cid}/summary-rebuild'
            self.assertEqual(client.post(path).status_code,200)
            self.assertEqual(client.get(path).json()['job']['status'],'queued')
            app.dependency_overrides[get_current_user]=lambda:SimpleNamespace(id='foreign-user')
            self.assertEqual(client.get(path).status_code,404)
            self.assertEqual(client.post(path).status_code,404)


class SummaryRebuildTest(SummaryRebuildCases, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        capacity_fixtures.ChatMemoryCapacityTest.setUpClass()
        cls.engine=capacity_fixtures.ChatMemoryCapacityTest.engine

    @classmethod
    def tearDownClass(cls):
        cls.engine.dispose()


@unittest.skipUnless(os.getenv('TEST_POSTGRES_URL'), '需要隔离PostgreSQL')
class SummaryRebuildPostgresTest(SummaryRebuildCases, unittest.TestCase):
    def test_delete_conversation_cascades_jobs_and_rejects_late_result(self):
        self.history()
        self.checkpoint(80)
        jid=self.enqueue()
        claim=self.worker.claim()
        data=self.worker.load(claim)
        with self.sessions() as db:
            db.delete(db.get(Conversation,self.cid))
            db.commit()
        self.assertFalse(self.worker.complete(claim,data,'已删除会话的迟到摘要'))
        with self.sessions() as db:
            self.assertIsNone(db.get(Job,jid))
            self.assertEqual(list(db.scalars(select(Checkpoint).where(Checkpoint.conversation_id==self.cid))),[])

    def test_concurrent_workers_claim_only_once(self):
        self.history()
        jid=self.enqueue()
        other=SummaryRebuildWorker(session_factory=self.sessions)
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims=list(pool.map(lambda worker: worker.claim(),[self.worker,other]))
        winners=[claim for claim in claims if claim]
        self.assertEqual(len(winners),1)
        self.assertEqual(winners[0].id,jid)

    @classmethod
    def setUpClass(cls):
        lifecycle_fixtures.ContextSummaryPostgresTest.setUpClass()
        cls.engine=lifecycle_fixtures.ContextSummaryPostgresTest.engine

    @classmethod
    def tearDownClass(cls):
        lifecycle_fixtures.ContextSummaryPostgresTest.tearDownClass()
