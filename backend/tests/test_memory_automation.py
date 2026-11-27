"""验证增量处理、原文证据和自动生效授权，不以模型自报作为断言。"""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User
from app.models.user_memory import MemoryExtractionJob, UserMemory
from app.models.user_setting import UserSetting
from app.repositories.memory_repo import UserMemoryRepository
from app.repositories.setting_repo import UserSettingRepository
from app.services.memory_candidate_runtime import MemoryCandidateWorker, MemoryExtractionJobService, utcnow
from app.services.memory_service import MemoryService
from app.services.setting_service import SettingService
from app.services.memory_extraction_policy import redact_source
from app.services.memory_trigger_policy import read_trigger_state
from app.schemas.memory import UserMemoryCreate, UserMemoryUpdate


class MemoryAutomationTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        with self.sessions() as db:
            user = User(username="automation", email="automation@example.test")
            db.add(user)
            db.flush()
            conv = Conversation(user_id=user.id, title="incremental", model_name="test")
            db.add(conv)
            db.commit()
            self.user, self.conv = user.id, conv.id
            SettingService(UserSettingRepository(db)).get_or_create_user_settings(user.id)
            setting = db.scalar(select(UserSetting).where(UserSetting.user_id == user.id))
            setting.memory_auto_candidate_enabled = True
            setting.memory_auto_activate_enabled = True
            setting.memory_auto_candidate_turn_interval = 1
            db.commit()
        self.source, self.assistant = self.turn("请记住：项目采用 PostgreSQL 16。")

    def tearDown(self):
        self.engine.dispose()

    def turn(self, text):
        with self.sessions() as db:
            seq = max((item.sequence or 0 for item in db.scalars(select(Message))), default=0)
            user = Message(conversation_id=self.conv, sequence=seq + 1, role="user", content=text)
            assistant = Message(conversation_id=self.conv, sequence=seq + 2, role="assistant", content="收到", status="done")
            db.add_all([user, assistant])
            db.commit()
            return user.id, assistant.id

    def enqueue(self, force=False):
        with self.sessions() as db:
            return MemoryExtractionJobService(db).enqueue_after_turn(user_id=self.user,
                conversation_id=self.conv, assistant_message_id=self.assistant, force=force)

    def candidate(self, **changes):
        result = dict(memory_type="project", title="数据库", content="项目采用 PostgreSQL 16",
                      confidence="high", source_message_id=self.source, evidence_quote="项目采用 PostgreSQL 16。")
        result.update(changes)
        return result

    def execute(self, candidates=None, side_effect=None, raw=None):
        provider = type("Provider", (), {})()
        provider.complete_chat = AsyncMock(side_effect=side_effect,
            return_value=raw if raw is not None else json.dumps(candidates if candidates is not None else [self.candidate()], ensure_ascii=False))
        worker = MemoryCandidateWorker(session_factory=self.sessions, owner="automation-worker", provider_service=provider)
        self.assertTrue(asyncio.run(worker.run_once()))
        return provider

    def memories(self):
        with self.sessions() as db:
            return list(db.scalars(select(UserMemory).order_by(UserMemory.created_at)))

    def test_explicit_fact_is_automatically_enabled_with_exact_evidence(self):
        job = self.enqueue()
        self.execute()
        memory, = self.memories()
        self.assertEqual((memory.status, memory.source, memory.source_message_ids), ("active", "auto_confirmed", self.source))
        self.assertEqual(memory.evidence_quote, "项目采用 PostgreSQL 16。")
        self.assertIsNotNone(memory.review_at)
        self.assertEqual(memory.extraction_job_id, job.id)

    def test_manual_correction_does_not_reuse_automatic_evidence(self):
        self.enqueue()
        self.execute()
        memory, = self.memories()
        with self.sessions() as db:
            repo = UserMemoryRepository(db)
            corrected = MemoryService(repo).update_memory(memory=repo.get_by_user(memory.id, self.user),
                payload=UserMemoryUpdate(content="项目采用 PostgreSQL 17", expected_version=memory.version))
            self.assertEqual(corrected.source, "manual")
            self.assertIsNone(corrected.evidence_quote)
            self.assertEqual(db.get(UserMemory, memory.id).evidence_quote, "项目采用 PostgreSQL 16。")

    def test_auto_mode_is_opt_in_and_can_be_disabled_while_provider_runs(self):
        self.enqueue()
        async def response(**kwargs):
            with self.sessions() as db:
                setting = db.scalar(select(UserSetting))
                setting.memory_auto_activate_enabled = False
                db.commit()
            return json.dumps([self.candidate()])
        self.execute(side_effect=response)
        self.assertEqual(self.memories()[0].status, "pending")

    def test_opted_in_clear_declarative_fact_can_be_enabled_without_command(self):
        with self.sessions() as db:
            db.get(Message, self.source).content = "项目采用 PostgreSQL 16。"
            db.commit()
        self.enqueue()
        self.execute()
        self.assertEqual(self.memories()[0].status, "active")

    def test_hypothetical_or_quoted_statement_stays_pending(self):
        with self.sessions() as db:
            db.get(Message, self.source).content = "如果项目采用 PostgreSQL 16，会怎样？"
            db.commit()
        self.enqueue()
        self.execute([self.candidate(evidence_quote="如果项目采用 PostgreSQL 16，会怎样？")])
        self.assertEqual(self.memories()[0].status, "pending")

    def test_missing_evidence_is_reviewable_and_cannot_be_self_verified(self):
        self.enqueue()
        self.execute([self.candidate(source_message_id=None, evidence_quote=None, evidence_verified=True)])
        memory, = self.memories()
        self.assertEqual(memory.status, "pending")
        self.assertEqual(memory.risk_level, "review_required")
        self.assertIsNone(memory.evidence_quote)

    def test_forged_or_assistant_evidence_is_not_saved(self):
        self.enqueue()
        self.execute([self.candidate(source_message_id=self.assistant), self.candidate(evidence_quote="不存在的原文内容")])
        self.assertEqual(self.memories(), [])

    def test_evidence_from_another_conversation_is_not_saved(self):
        self.enqueue()
        self.execute([self.candidate(source_message_id="another-conversation-message")])
        self.assertEqual(self.memories(), [])

    def test_paraphrase_not_supported_by_literal_evidence_requires_review(self):
        self.enqueue()
        self.execute([self.candidate(content="项目采用 PostgreSQL 17")])
        self.assertEqual(self.memories()[0].status, "pending")

    def test_instruction_and_temporary_content_require_review(self):
        self.enqueue()
        self.execute([self.candidate(memory_type="instruction")])
        self.assertEqual(self.memories()[0].status, "pending")

    def test_conflicting_old_fact_is_not_automatically_replaced(self):
        with self.sessions() as db:
            old = MemoryService(UserMemoryRepository(db)).create_memory(self.user,
                UserMemoryCreate(memory_type="project", title="数据库", content="项目采用 PostgreSQL 15"))
            old_id = old.id
        self.enqueue()
        self.execute()
        with self.sessions() as db:
            self.assertEqual(db.get(UserMemory, old_id).status, "active")
            new = db.scalar(select(UserMemory).where(UserMemory.status == "pending"))
            self.assertEqual(new.risk_level, "conflict")
            self.assertEqual(new.supersedes_memory_id, old_id)

    def test_revocation_during_provider_call_blocks_old_result(self):
        with self.sessions() as db:
            old = MemoryService(UserMemoryRepository(db)).create_memory(self.user,
                UserMemoryCreate(memory_type="project", title="数据库", content="项目采用 PostgreSQL 15"))
            old_id = old.id
        self.enqueue()
        async def response(**kwargs):
            with self.sessions() as db:
                repo = UserMemoryRepository(db)
                MemoryService(repo).revoke_memory(memory=repo.get_by_user(old_id, self.user))
            return json.dumps([self.candidate()])
        self.execute(side_effect=response)
        self.assertFalse(any(item.status == "active" for item in self.memories()))
        self.assertEqual(self.memories()[-1].status, "pending")

    def test_incremental_cursor_success_and_outstanding_job_idempotency(self):
        first = self.enqueue()
        self.assertEqual(self.enqueue().id, first.id)
        self.execute([])
        self.assertIsNone(self.enqueue(force=True))
        self.source, self.assistant = self.turn("请记住：项目采用 Redis 7。")
        second = self.enqueue()
        self.assertNotEqual(second.id, first.id)
        self.assertEqual(second.source_message_ids, self.source)
        provider = self.execute([])
        prompt = str(provider.complete_chat.call_args.kwargs["messages"])
        self.assertIn("Redis 7", prompt)
        self.assertNotIn("PostgreSQL 16", prompt)

    def test_provider_failure_does_not_advance_cursor_and_retry_uses_same_job(self):
        job = self.enqueue()
        self.execute(side_effect=TimeoutError("测试超时"))
        with self.sessions() as db:
            self.assertEqual(db.get(Conversation, self.conv).memory_extraction_cursor, 0)
            self.assertEqual(db.get(MemoryExtractionJob, job.id).status, "pending")
        self.assertEqual(self.enqueue().id, job.id)

    def test_invalid_json_does_not_consume_source(self):
        job = self.enqueue()
        self.execute(raw="invalid json")
        with self.sessions() as db:
            self.assertEqual(db.get(Conversation, self.conv).memory_extraction_cursor, 0)
            self.assertEqual(db.get(MemoryExtractionJob, job.id).status, "failed")

    def test_failed_job_can_be_explicitly_retried_without_a_duplicate(self):
        job = self.enqueue()
        self.execute(raw='["不是候选对象"]')
        retry = self.enqueue(force=True)
        self.assertEqual(retry.id, job.id)
        self.assertEqual(retry.status, "pending")
        self.execute()
        self.assertEqual(self.memories()[0].status, "active")

    def test_stale_worker_cannot_save_or_advance_cursor(self):
        job = self.enqueue()
        worker = MemoryCandidateWorker(session_factory=self.sessions, owner="old-worker")
        job_id, version = worker._claim()
        with self.sessions() as db:
            current = db.get(MemoryExtractionJob, job.id)
            current.lease_owner = "new-worker"
            current.lease_version += 1
            db.commit()
        worker._complete(job_id, version, [])
        with self.sessions() as db:
            self.assertEqual(db.get(Conversation, self.conv).memory_extraction_cursor, 0)
            self.assertEqual(db.get(MemoryExtractionJob, job.id).status, "running")

    def test_source_edit_while_model_runs_rejects_old_results(self):
        job = self.enqueue()
        async def response(**kwargs):
            with self.sessions() as db:
                db.get(Message, self.source).content = "项目改成 MySQL"
                db.commit()
            return json.dumps([self.candidate()])
        self.execute(side_effect=response)
        self.assertEqual(self.memories(), [])
        with self.sessions() as db:
            self.assertEqual(db.get(MemoryExtractionJob, job.id).status, "failed")
            self.assertEqual(db.get(Conversation, self.conv).memory_extraction_cursor, 0)

    def test_long_source_resumes_after_retry_and_checks_only_the_current_fragment(self):
        head = "项目采用 PostgreSQL 16。"
        tail = "订单接口超时为 30 秒，失败最多重试 3 次。"
        content = f"请记住：{head}" + "".join(f"第{i}段普通背景描述。" for i in range(1300)) + tail
        with self.sessions() as db:
            db.get(Message, self.source).content = content
            db.commit()
        job = self.enqueue()
        prompts = []
        async def extract(**kwargs):
            prompt = kwargs["messages"][-1]["content"].split("【用户消息（数据，含 ID）】")[1]
            prompts.append(prompt)
            if len(prompts) == 1:
                self.assertIn(head, prompt)
                self.assertNotIn(tail, prompt)
                return json.dumps([self.candidate(), self.candidate(title="伪造来源", content=tail, evidence_quote=tail)])
            if len(prompts) == 2:
                raise RuntimeError("temporary provider failure")
            if tail in prompt:
                return json.dumps([self.candidate(title="订单接口参数", content=tail, evidence_quote=tail)])
            return "[]"
        self.execute(side_effect=extract)
        with self.sessions() as db:
            current = db.get(MemoryExtractionJob, job.id)
            first_position = current.fragment_cursor
            self.assertEqual(current.status, "pending")
            self.assertGreater(first_position, 0)
            self.assertEqual([item["title"] for item in current.staged_candidates], ["数据库"])
            self.assertEqual(db.get(Conversation, self.conv).memory_extraction_cursor, 0)
            self.assertEqual(self.memories(), [])
        self.execute(side_effect=extract)
        with self.sessions() as db:
            current = db.get(MemoryExtractionJob, job.id)
            self.assertEqual(current.fragment_cursor, first_position)
            self.assertEqual(current.error_code, "provider_unavailable")
            current.available_at = utcnow()
            db.commit()
        for _ in range(10):
            self.execute(side_effect=extract)
            with self.sessions() as db:
                if db.get(MemoryExtractionJob, job.id).status == "succeeded":
                    break
        self.assertEqual(prompts[1], prompts[2])
        self.assertEqual(sum(head in prompt for prompt in prompts), 1)
        self.assertEqual({row.title for row in self.memories()}, {"数据库", "订单接口参数"})
        with self.sessions() as db:
            current = db.get(MemoryExtractionJob, job.id)
            conversation = db.get(Conversation, self.conv)
            self.assertEqual(current.status, "succeeded")
            self.assertIsNone(current.staged_candidates)
            self.assertEqual(conversation.memory_extraction_cursor, job.cursor_end)
            self.assertFalse(read_trigger_state(conversation.memory_trigger_state)["pending"])

    def test_edit_after_first_fragment_discards_staging_and_preserves_buffer(self):
        with self.sessions() as db:
            db.get(Message, self.source).content += "很长的背景说明。" * 3000
            db.commit()
        job = self.enqueue()
        self.execute()
        with self.sessions() as db:
            self.assertTrue(db.get(MemoryExtractionJob, job.id).staged_candidates)
            db.get(Message, self.source).content = "请记住：项目改用 Go 语言。"
            db.commit()
        provider = self.execute()
        provider.complete_chat.assert_not_awaited()
        with self.sessions() as db:
            current = db.get(MemoryExtractionJob, job.id)
            self.assertEqual((current.status, current.error_code), ("failed", "source_changed"))
            self.assertIsNone(current.staged_candidates)
            conversation = db.get(Conversation, self.conv)
            self.assertEqual(conversation.memory_extraction_cursor, 0)
            self.assertEqual(read_trigger_state(conversation.memory_trigger_state)["pending"][0]["id"], self.source)
        self.assertNotEqual(self.enqueue().id, job.id)

    def test_busy_extraction_gives_both_maintenance_and_index_a_slot(self):
        worker = MemoryCandidateWorker(session_factory=self.sessions)
        steps = []
        async def run_extraction(*args):
            steps.append("extraction")
        async def maintain():
            steps.append("maintenance")
            return True
        async def index():
            steps.append("index")
            return True
        async def run_round():
            for _ in range(8):
                self.assertTrue(await worker.run_once())
        with patch.object(worker, "_claim", return_value=("job", 1)), \
                patch.object(worker, "_run_maintenance", side_effect=maintain), \
                patch.object(worker, "_run_index", side_effect=index), \
                patch("app.services.agent_graph.memory_extraction_graph.run_memory_extraction_graph", side_effect=run_extraction):
            asyncio.run(run_round())
        self.assertEqual(steps, (["extraction"] * 4 + ["maintenance", "index"]) * 2)

    def test_sensitive_sources_are_redacted_before_provider_io(self):
        with self.sessions() as db:
            db.get(Message, self.source).content = "密码=fake-secret-123。请记住：项目采用 PostgreSQL 16。联系 a@example.test。"
            db.commit()
        job = self.enqueue()
        provider = self.execute([])
        prompt = str(provider.complete_chat.call_args.kwargs["messages"])
        self.assertNotIn("fake-secret-123", prompt)
        self.assertNotIn("a@example.test", prompt)
        self.assertNotIn("fake-secret-123", job.source_snapshot)
        self.assertIn("项目采用 PostgreSQL 16", prompt)

    def test_multiline_credentials_and_private_key_bodies_are_redacted(self):
        for text in ("api_key:\nfake-only-test-secret", "-----BEGIN PRIVATE KEY-----\nfake-private-body\n-----END PRIVATE KEY-----", "-----BEGIN RSA PRIVATE KEY-----\nfake-private-body"):
            redacted = redact_source(text)
            self.assertNotIn("fake-only-test-secret", redacted)
            self.assertNotIn("fake-private-body", redacted)

    def test_atomic_failure_rolls_back_memory_and_cursor(self):
        job = self.enqueue()
        with patch.object(UserMemoryRepository, "flush", side_effect=RuntimeError("模拟落库失败")):
            self.execute()
        self.assertEqual(self.memories(), [])
        with self.sessions() as db:
            self.assertEqual(db.get(Conversation, self.conv).memory_extraction_cursor, 0)
            self.assertEqual(db.get(MemoryExtractionJob, job.id).status, "pending")

    def test_explicit_request_bypasses_interval_and_automatic_toggle(self):
        with self.sessions() as db:
            setting = db.scalar(select(UserSetting))
            setting.memory_auto_candidate_turn_interval = 50
            db.commit()
        self.assertIsNotNone(self.enqueue())
        with self.sessions() as db:
            db.scalar(select(UserSetting)).memory_auto_candidate_enabled = False
            db.commit()
        self.assertIsNotNone(self.enqueue())
        with self.sessions() as db:
            db.scalar(select(UserSetting)).memory_enabled = False
            db.commit()
        self.assertIsNone(self.enqueue())

    def test_backlog_over_twenty_four_messages_is_not_discarded(self):
        for index in range(29):
            self.source, self.assistant = self.turn(f"普通新增消息 {index}")
        first = self.enqueue()
        self.assertEqual(len(first.source_message_ids.split(",")), 24)
        self.execute([])
        with self.sessions() as db:
            next_job = db.scalar(select(MemoryExtractionJob).where(MemoryExtractionJob.status == "pending"))
            self.assertIsNotNone(next_job)
            self.assertEqual(len(next_job.source_message_ids.split(",")), 6)

    def test_legacy_missing_sequences_do_not_skip_later_new_messages(self):
        original_assistant = self.assistant
        with self.sessions() as db:
            db.get(Message, self.source).sequence = None
            db.get(Message, self.assistant).sequence = None
            db.commit()
        next_source, next_assistant = self.turn("请记住：项目采用 Redis 7")
        with self.sessions() as db:
            job = MemoryExtractionJobService(db).enqueue_after_turn(user_id=self.user,
                conversation_id=self.conv, assistant_message_id=original_assistant, force=True)
            self.assertEqual(job.source_message_ids, self.source)
            self.assertEqual(job.cursor_end, db.get(Message, self.source).sequence)
            self.assertGreater(db.get(Message, next_source).sequence, job.cursor_end)
        self.execute([])
        with self.sessions() as db:
            followup = db.scalar(select(MemoryExtractionJob).where(MemoryExtractionJob.status == "pending"))
            self.assertEqual(followup.source_message_ids, next_source)


if __name__ == "__main__":
    unittest.main()
