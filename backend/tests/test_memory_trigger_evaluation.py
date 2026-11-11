"""触发决策记录、标注评测和 buffer 语义门控回归。"""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy import create_engine, select, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.conversation import Conversation
from app.models.memory_trigger_decision import MemoryTriggerDecision
from app.models.message import Message
from app.models.user import User
from app.models.user_memory import MemoryExtractionJob, UserMemory
from app.models.project import Project
from app.models.user_setting import UserSetting
from app.repositories.setting_repo import UserSettingRepository
from app.services.memory_candidate_runtime import MemoryCandidateWorker, MemoryExtractionJobService
from app.services.memory_semantic_gate import parse_semantic_result
from app.services.memory_trigger_policy import POLICY_VERSION, classify_memory_signal
from app.services.setting_service import SettingService
from app.schemas.memory import MemoryTriggerLabel
from app.services.memory_trigger_review_service import label_decision
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.api.deps import get_current_user, get_db
from app.api.routes.memories import router


class MemoryTriggerEvaluationTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        with self.sessions() as db:
            user = User(username="trigger-eval", email="trigger-eval@example.test")
            db.add(user)
            db.flush()
            conv = Conversation(user_id=user.id, model_name="test")
            db.add(conv)
            db.commit()
            self.user, self.conv = user.id, conv.id
            SettingService(UserSettingRepository(db)).get_or_create_user_settings(user.id)
            setting = db.scalar(select(UserSetting))
            setting.memory_auto_candidate_enabled = True
            setting.memory_auto_activate_enabled = False
            setting.memory_semantic_trigger_enabled = True
            setting.memory_auto_candidate_turn_interval = 4
            db.commit()

    def tearDown(self):
        self.engine.dispose()

    def turn(self, text):
        with self.sessions() as db:
            seq = max((row.sequence or 0 for row in db.scalars(select(Message))), default=0)
            source = Message(conversation_id=self.conv, sequence=seq + 1, role="user", content=text)
            assistant = Message(conversation_id=self.conv, sequence=seq + 2, role="assistant", status="done", content="收到")
            db.add_all([source, assistant])
            db.commit()
            return source.id, assistant.id

    def enqueue(self, assistant_id):
        with self.sessions() as db:
            return MemoryExtractionJobService(db).enqueue_after_turn(user_id=self.user,
                conversation_id=self.conv, assistant_message_id=assistant_id)

    def worker(self, response):
        provider = type("Provider", (), {"complete_chat": AsyncMock(return_value=response)})()
        return MemoryCandidateWorker(session_factory=self.sessions, owner="trigger-eval", provider_service=provider), provider

    def test_decision_records_never_store_message_content_and_job_link_is_bound(self):
        _, assistant = self.turn("团队每周三同步")
        _, assistant = self.turn("项目最终决定使用 PostgreSQL。")
        job = self.enqueue(assistant)
        with self.sessions() as db:
            rows = list(db.scalars(select(MemoryTriggerDecision).where(MemoryTriggerDecision.conversation_id == self.conv)))
            self.assertGreaterEqual(len(rows), 2)
            self.assertTrue(all(row.source_digest and row.policy_version == POLICY_VERSION for row in rows))
            self.assertTrue(all("PostgreSQL" not in (row.context_json or "") for row in rows))
            linked = [row for row in rows if row.job_id == job.id]
            self.assertTrue(linked)
            self.assertTrue(all(row.job_created for row in linked))

    def test_edited_message_creates_new_digest_decision(self):
        source, assistant = self.turn("项目可能采用 Redis。")
        self.enqueue(assistant)
        with self.sessions() as db:
            db.get(Message, source).content = "项目最终决定采用 PostgreSQL。"
            db.commit()
        _, assistant = self.turn("补充一轮")
        self.enqueue(assistant)
        with self.sessions() as db:
            rows = list(db.scalars(select(MemoryTriggerDecision).where(MemoryTriggerDecision.message_id == source)))
            self.assertEqual(len(rows), 2)
            self.assertNotEqual(rows[0].source_digest, rows[1].source_digest)

    def test_buffer_semantic_skip_finishes_without_extraction_candidate(self):
        self.turn("团队每周三同步")
        self.turn("笔记按月份存放")
        self.turn("开发文档集中归档")
        _, assistant = self.turn("其他约定另行补充")
        job = self.enqueue(assistant)
        worker, provider = self.worker(json.dumps({"action": "skip", "confidence": "high", "reason": "普通工作记录"}))
        self.assertTrue(asyncio.run(worker.run_once()))
        self.assertEqual(provider.complete_chat.await_count, 1)
        with self.sessions() as db:
            row = db.get(MemoryExtractionJob, job.id)
            self.assertEqual((row.status, row.semantic_action, row.result_count), ("succeeded", "skip", 0))
            decision = db.scalar(select(MemoryTriggerDecision).where(MemoryTriggerDecision.job_id == job.id))
            self.assertEqual((decision.semantic_action, decision.semantic_model_called), ("skip", True))

    def test_buffer_semantic_extract_then_original_extraction_runs(self):
        self.turn("团队每周三同步")
        self.turn("笔记按月份存放")
        self.turn("开发文档集中归档")
        _, assistant = self.turn("其他约定另行补充")
        job = self.enqueue(assistant)
        candidate = json.dumps([])
        provider = type("Provider", (), {"complete_chat": AsyncMock(side_effect=[
            json.dumps({"action": "extract_now", "confidence": "high", "reason": "稳定约定"}), candidate
        ])})()
        worker = MemoryCandidateWorker(session_factory=self.sessions, owner="trigger-eval", provider_service=provider)
        self.assertTrue(asyncio.run(worker.run_once()))
        self.assertEqual(provider.complete_chat.await_count, 2)
        with self.sessions() as db:
            row = db.get(MemoryExtractionJob, job.id)
            self.assertEqual((row.status, row.semantic_action), ("succeeded", "extract_now"))

    def test_semantic_invalid_result_is_conservative_and_does_not_activate_by_itself(self):
        self.assertEqual(parse_semantic_result("not json")[:2], ("buffer", "low"))
        self.turn("团队每周三同步")
        self.turn("笔记按月份存放")
        self.turn("开发文档集中归档")
        _, assistant = self.turn("其他约定另行补充")
        job = self.enqueue(assistant)
        provider = type("Provider", (), {"complete_chat": AsyncMock(side_effect=["bad", "[]"])})()
        worker = MemoryCandidateWorker(session_factory=self.sessions, owner="trigger-eval", provider_service=provider)
        self.assertTrue(asyncio.run(worker.run_once()))
        with self.sessions() as db:
            row = db.get(MemoryExtractionJob, job.id)
            self.assertEqual(row.status, "succeeded")
            self.assertEqual(row.semantic_action, "buffer")
            self.assertFalse(row.semantic_model_called is False)

    def test_rules_dataset_has_explicit_mismatch_samples(self):
        self.assertEqual(classify_memory_signal("我在论文中看到一句‘我的项目采用 PostgreSQL’。").action, "skip")
        self.assertEqual(classify_memory_signal("关于数据库，我们接下来统一按新的方案实施。").action, "buffer")

    def test_recent_context_is_recorded_but_does_not_promote_ambiguous_followup(self):
        self.assertEqual(classify_memory_signal("关于数据库，我们接下来统一按新的方案实施。").action, "buffer")
        self.turn("项目最终决定使用 PostgreSQL。")
        source, assistant = self.turn("关于数据库，我们接下来统一按新的方案实施。")
        self.enqueue(assistant)
        with self.sessions() as db:
            decision = db.scalar(select(MemoryTriggerDecision).where(MemoryTriggerDecision.message_id == source))
            self.assertEqual(decision.action, "buffer")
            context = json.loads(decision.context_json)
            self.assertTrue(context["recent_decision_signal"])

    def batch(self):
        for text in ("团队每周三同步", "笔记按月份存放", "开发文档集中归档", "其他约定另行补充"):
            _, assistant = self.turn(text)
        return self.enqueue(assistant)

    def test_disabled_gate_keeps_original_extraction_and_new_user_default_is_off(self):
        with self.sessions() as db:
            db.scalar(select(UserSetting)).memory_semantic_trigger_enabled = False
            db.commit()
        job = self.batch()
        worker, provider = self.worker("[]")
        asyncio.run(worker.run_once())
        self.assertEqual(provider.complete_chat.await_count, 1)
        with self.sessions() as db:
            self.assertFalse(db.get(MemoryExtractionJob, job.id).semantic_model_called)
        self.assertFalse(SettingService._build_default_setting("new").memory_semantic_trigger_enabled)

    def test_low_confidence_skip_falls_back_instead_of_leaving_running_job(self):
        job = self.batch()
        worker, provider = self.worker("[]")
        provider.complete_chat.side_effect = [json.dumps({"action": "skip", "confidence": "low", "reason": "未知"}), "[]"]
        asyncio.run(worker.run_once())
        self.assertEqual(provider.complete_chat.await_count, 2)
        with self.sessions() as db:
            self.assertEqual(db.get(MemoryExtractionJob, job.id).status, "succeeded")

    def test_semantic_timeout_falls_back_and_does_not_activate(self):
        job = self.batch()
        worker, provider = self.worker("[]")
        provider.complete_chat.side_effect = [TimeoutError(), "[]"]
        asyncio.run(worker.run_once())
        self.assertEqual(provider.complete_chat.await_count, 2)
        with self.sessions() as db:
            self.assertEqual(db.get(MemoryExtractionJob, job.id).status, "succeeded")

    def test_high_buffer_keeps_sources_without_busy_loop_then_accepts_new_input(self):
        job = self.batch()
        worker, provider = self.worker(json.dumps({"action": "buffer", "confidence": "high", "reason": "缺上下文"}))
        asyncio.run(worker.run_once())
        self.assertFalse(asyncio.run(worker.run_once()))
        with self.sessions() as db:
            conv = db.get(Conversation, self.conv)
            self.assertEqual(conv.memory_extraction_cursor, 0)
            self.assertEqual(len(json.loads(conv.memory_trigger_state)["pending"]), 4)
        _, assistant = self.turn("补充一下约定")
        next_job = self.enqueue(assistant)
        self.assertNotEqual(job.id, next_job.id)
        self.assertEqual(provider.complete_chat.await_count, 1)

    def test_user_cooldown_skips_extra_call_but_not_original_extraction(self):
        job = self.batch()
        with self.sessions() as db:
            db.scalar(select(UserSetting)).memory_semantic_last_checked_at = datetime.now(timezone.utc)
            db.commit()
        worker, provider = self.worker("[]")
        asyncio.run(worker.run_once())
        self.assertEqual(provider.complete_chat.await_count, 1)
        with self.sessions() as db:
            self.assertFalse(db.get(MemoryExtractionJob, job.id).semantic_model_called)

    def test_lost_lease_during_semantic_return_stops_extraction(self):
        job = self.batch()
        worker, provider = self.worker("[]")
        async def response(**kwargs):
            with self.sessions() as db:
                db.get(MemoryExtractionJob, job.id).lease_version += 1
                db.commit()
            return '{"action":"skip","confidence":"high","reason":"x"}'
        provider.complete_chat.side_effect = response
        asyncio.run(worker.run_once())
        self.assertEqual(provider.complete_chat.await_count, 1)
        with self.sessions() as db:
            self.assertIsNone(db.get(MemoryExtractionJob, job.id).semantic_action)
            self.assertEqual(db.get(Conversation, self.conv).memory_extraction_cursor, 0)

    def test_edited_source_during_gate_does_not_consume_cursor(self):
        job = self.batch()
        worker, provider = self.worker("[]")
        async def response(**kwargs):
            with self.sessions() as db:
                db.get(Message, job.source_message_ids.split(",")[0]).content = "修改后的内容"
                db.commit()
            return '{"action":"skip","confidence":"high","reason":"x"}'
        provider.complete_chat.side_effect = response
        asyncio.run(worker.run_once())
        with self.sessions() as db:
            self.assertEqual(db.get(MemoryExtractionJob, job.id).status, "failed")
            self.assertEqual(db.get(Conversation, self.conv).memory_extraction_cursor, 0)

    def test_pending_job_still_audits_new_messages_without_linking_them_to_old_job(self):
        _, assistant = self.turn("我的项目采用 PostgreSQL。")
        first = self.enqueue(assistant)
        source, assistant = self.turn("团队每周三同步")
        self.assertEqual(self.enqueue(assistant).id, first.id)
        with self.sessions() as db:
            row = db.scalar(select(MemoryTriggerDecision).where(MemoryTriggerDecision.message_id == source))
            self.assertEqual(row.outcome, "deferred_existing_job")
            self.assertIsNone(row.job_id)

    def test_decision_is_idempotent_for_same_observation_and_can_be_labeled(self):
        source, assistant = self.turn("团队每周三同步")
        self.enqueue(assistant)
        self.enqueue(assistant)
        with self.sessions() as db:
            rows = list(db.scalars(select(MemoryTriggerDecision).where(MemoryTriggerDecision.message_id == source)))
            self.assertEqual(len(rows), 1)
            labeled = label_decision(db, self.user, rows[0].id,
                MemoryTriggerLabel(expected_action="extract", review_reason="missed_trigger"))
            self.assertEqual(labeled.expected_action, "extract_now")
            self.assertIsNotNone(labeled.reviewed_at)
            with self.assertRaises(LookupError):
                label_decision(db, "not-owner", rows[0].id,
                    MemoryTriggerLabel(expected_action="skip", review_reason="false_trigger"))

    def test_api_ownership_and_no_raw_text_or_free_form_review(self):
        _, assistant = self.turn("我的项目采用 PostgreSQL。")
        self.enqueue(assistant)
        app = FastAPI()
        app.include_router(router, prefix="/api")
        def database():
            with self.sessions() as db:
                yield db
        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id=self.user)
        with TestClient(app) as client:
            response = client.get("/api/memories/trigger-decisions")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertNotIn("PostgreSQL", response.text)
            row = response.json()[0]
            self.assertNotIn("source_digest", row)
            path = f"/api/memories/trigger-decisions/{row['id']}/label"
            self.assertEqual(client.post(path, json={"expected_action": "extract", "review_reason": "correct"}).status_code, 200)
            self.assertEqual(client.post(path, json={"expected_action": "skip", "review_reason": "secret text"}).status_code, 422)
            app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="not-owner")
            self.assertEqual(client.get("/api/memories/trigger-decisions").json(), [])
            self.assertEqual(client.post(path, json={"expected_action": "skip", "review_reason": "correct"}).status_code, 404)

    def test_binary_metrics_are_separate_from_action_accuracy(self):
        from scripts.evaluate_memory_trigger_policy import metrics
        result = metrics([{"expected": "extract_now", "predicted": "revise"},
            {"expected": "buffer", "predicted": "extract_now"},
            {"expected": "extract_now", "predicted": "buffer"}, {"expected": "skip", "predicted": "skip"}])
        self.assertEqual(result["trigger_precision"], 0.5)
        self.assertEqual(result["trigger_recall"], 0.5)
        self.assertEqual(result["action_accuracy"], 0.25)

    def test_context_selects_are_bounded_for_many_sources(self):
        with self.sessions() as db:
            db.scalar(select(UserSetting)).memory_auto_candidate_turn_interval = 50
            db.commit()
        for index in range(30):
            _, assistant = self.turn(f"普通待定记录 {index}")
        reads = []
        def count_select(connection, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                reads.append(statement)
        event.listen(self.engine, "before_cursor_execute", count_select)
        try:
            self.assertIsNone(self.enqueue(assistant))
        finally:
            event.remove(self.engine, "before_cursor_execute", count_select)
        self.assertLessEqual(len(reads), 14, "上下文不能每个来源重复查询")

    def test_saved_skip_resumes_without_another_model_call(self):
        job = self.batch()
        worker, provider = self.worker("[]")
        job_id, version = worker._claim()
        self.assertTrue(worker._reserve_semantic_call(job_id, version))
        self.assertTrue(worker._record_semantic_decision(job_id, version, "skip", "high"))
        from app.services.agent_graph.memory_extraction_graph import run_memory_extraction_graph
        asyncio.run(run_memory_extraction_graph(worker, job_id, version))
        provider.complete_chat.assert_not_called()
        with self.sessions() as db:
            self.assertEqual(db.get(MemoryExtractionJob, job.id).status, "succeeded")

    def test_other_project_memory_is_absent_from_features_and_semantic_prompt(self):
        with self.sessions() as db:
            project = Project(user_id=self.user, name="foreign")
            db.add(project)
            db.flush()
            db.add(UserMemory(user_id=self.user, project_id=project.id, memory_type="project",
                title="foreign-only-marker", content="其他项目的私有约定", status="active", is_enabled=True))
            db.commit()
        self.batch()
        worker, _ = self.worker("[]")
        job_id, version = worker._claim()
        snapshot = worker._load_snapshot(job_id, version)
        self.assertNotIn("foreign-only-marker", str(snapshot["semantic_call"]["messages"]))
        with self.sessions() as db:
            for row in db.scalars(select(MemoryTriggerDecision)):
                self.assertEqual(json.loads(row.context_json)["active_memory_count"], 0)

    def test_gate_cannot_revoke_or_activate_memories(self):
        for action in ("revoke", "revise", "active", "execute_tool"):
            self.assertEqual(parse_semantic_result(json.dumps({"action": action, "confidence": "high", "reason": "x"}))[:2],
                ("buffer", "low"))

    def test_disabled_memory_still_records_reason_without_job(self):
        with self.sessions() as db:
            db.scalar(select(UserSetting)).memory_enabled = False
            db.commit()
        _, assistant = self.turn("请记住：项目使用 PostgreSQL。")
        self.assertIsNone(self.enqueue(assistant))
        with self.sessions() as db:
            row = db.scalar(select(MemoryTriggerDecision))
            self.assertEqual(row.outcome, "memory_disabled")
            self.assertFalse(row.job_created)

    def test_full_buffer_falls_back_to_extraction_so_new_sources_are_not_starved(self):
        for index in range(50):
            _, assistant = self.turn(f"待定工作记录 {index}")
        self.enqueue(assistant)
        worker, provider = self.worker("[]")
        provider.complete_chat.side_effect = ['{"action":"buffer","confidence":"high","reason":"等待补充"}', "[]"]
        self.assertTrue(asyncio.run(worker.run_once()))
        self.assertEqual(provider.complete_chat.await_count, 2)
        with self.sessions() as db:
            self.assertEqual(len(json.loads(db.get(Conversation, self.conv).memory_trigger_state)["pending"]), 26)

    def test_disabling_gate_after_defer_does_not_block_new_input_behind_completed_batch(self):
        for index in range(24):
            _, assistant = self.turn(f"待定工作记录 {index}")
        first = self.enqueue(assistant)
        worker, _ = self.worker('{"action":"buffer","confidence":"high","reason":"等待补充"}')
        asyncio.run(worker.run_once())
        with self.sessions() as db:
            db.scalar(select(UserSetting)).memory_semantic_trigger_enabled = False
            db.commit()
        source, assistant = self.turn("新的补充记录")
        new = self.enqueue(assistant)
        self.assertNotEqual(new.id, first.id)
        self.assertIn(source, new.source_message_ids.split(","))


if __name__ == "__main__":
    unittest.main()
