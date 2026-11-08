"""普通Chat工具恢复：真实文件工具、独立Session和故障窗口，不访问外部模型。"""

import asyncio
from collections import Counter
from copy import deepcopy
from datetime import timedelta
import json
import unittest
from unittest.mock import patch

from sqlalchemy import select

from app.models.agent_runtime import AgentArtifact, AgentCheckpoint, AgentRun, AgentStep
from app.models.chat_run import ChatRun, ChatRunEvent
from app.models.conversation import Conversation
from app.models.project import Project
from app.models.project_file import ProjectFile
from app.models.tool_config import WorkspaceAgentPolicy, WorkspaceToolSetting
from app.services.chat_run_service import ChatLeaseLost, ChatRunService
from app.services.chat_tool_runtime import ChatToolPersistence, current_tool_persistence
from app.services.chat_tool_snapshot import ChatToolSnapshotCodec
from app.services.external_context_service import ExternalContextService
from app.services.runtime_lease import utcnow
from app.services.tools.executor import ToolExecutor
from app.services.tools.schemas import ExternalSource, PlannedToolCall, ToolCallResult, ToolPlan, ToolResultBinding
from app.services.tools import runtime_codec as codec
from backend.tests import test_chat_runs as chat_unit


class FixedPlanner:
    def __init__(self, calls, *, more=False):
        self.calls, self.count, self.more = calls, 0, more

    async def plan(self, **kwargs):
        self.count += 1
        return ToolPlan("saved-plan", "test", True, bool(self.calls), deepcopy(self.calls), need_more_rounds=self.more)


class ChatToolRecoveryTest(unittest.IsolatedAsyncioTestCase):
    setUp = chat_unit.ChatRunTest.setUp
    tearDown = chat_unit.ChatRunTest.tearDown
    create = chat_unit.ChatRunTest.create
    call = chat_unit.ChatRunTest.call
    snapshot = chat_unit.ChatRunTest.snapshot

    def fixture(self):
        with self.sessions() as db:
            db.add(Project(id="p", user_id="u", name="测试工作区"))
            db.flush()
            db.add(Conversation(id="c", user_id="u", project_id="p", title="恢复测试", model_name="test"))
            db.add(ProjectFile(id="f", user_id="u", project_id="p", kind="text", mime_type="text/plain",
                file_name="notes.txt", storage_key="tests/notes", parsed_text="有效资料：项目采用短事务与明确审批。"))
            db.commit()
        rid = self.create(conversation_id="c")
        claim = self.call("claim_next", "A")
        persistence = ChatToolPersistence(self.sessions, claim)
        persistence.initialize()
        return rid, claim, persistence

    def tool(self, cid, key="workspace.files.read", *, depends=None):
        return PlannedToolCall(cid, key, "workspace", "workspace_file", key, 1, "读取已授权资料",
            arguments={"file_id": "f"} if key != "workspace.files.list" else {}, depends_on=depends or [])

    async def context(self, persistence, planner):
        token = current_tool_persistence.set(persistence)
        try:
            with self.sessions() as db:
                chat = db.get(ChatRun, persistence.claim.run_id)
                service = ExternalContextService(db=db, user_id="u", project_id="p", conversation_id="c",
                    assistant_message_id=chat.assistant_message_id, planner=planner)
                return await service.build_context(query="仅阅读工作区资料，不联网，汇总已核实内容", enabled=True, max_chars=4000)
        finally:
            current_tool_persistence.reset(token)

    def expire_and_claim(self, rid, owner="B"):
        with self.sessions() as db:
            db.get(ChatRun, rid).lease_expires_at = utcnow() - timedelta(seconds=1)
            db.commit()
        claim = self.call("claim_next", owner)
        self.assertIsNotNone(claim)
        persistence = ChatToolPersistence(self.sessions, claim)
        persistence.initialize()
        return persistence

    async def test_completed_upstream_reused_and_inflight_read_retried_after_takeover(self):
        rid, old, first = self.fixture()
        started = asyncio.Event()
        count = Counter()
        original = ToolExecutor._execute
        async def execute(executor, call):
            count[call.call_id] += 1
            if call.call_id == "B" and count["B"] == 1:
                started.set()
                await asyncio.Event().wait()
            return await original(executor, call)
        plan = FixedPlanner([self.tool("A"), self.tool("B", "workspace.files.list", depends=["A"])])
        with patch.object(ToolExecutor, "_execute", execute):
            task = asyncio.create_task(self.context(first, plan))
            await asyncio.wait_for(started.wait(), 10)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            second = self.expire_and_claim(rid)
            self.assertFalse(self.call("heartbeat", old))
            untouched_planner = FixedPlanner([])
            result = await self.context(second, untouched_planner)
        self.assertEqual(count, {"A": 1, "B": 2})
        self.assertEqual(untouched_planner.count, 0)
        self.assertIn("短事务", result.context_text)
        self.assertTrue(any(event.type == "tool_result_reused" for event in result.tool_events))
        with self.sessions() as db:
            steps = list(db.scalars(select(AgentStep).where(AgentStep.sequence > 0).order_by(AgentStep.sequence)))
            self.assertEqual([s.attempts for s in steps], [1, 2])
            self.assertTrue(all(s.status == "succeeded" for s in steps))
            self.assertEqual(len(list(db.scalars(select(AgentArtifact)))), 2)

    async def test_finished_loop_does_not_replan_or_reexecute_after_restart(self):
        rid, _, first = self.fixture()
        planner = FixedPlanner([self.tool("A")])
        await self.context(first, planner)
        second = self.expire_and_claim(rid)
        with patch.object(ToolExecutor, "_execute", side_effect=AssertionError("不应再次访问工具")):
            untouched = FixedPlanner([])
            restored = await self.context(second, untouched)
        self.assertEqual(untouched.count, 0)
        self.assertIn("短事务", restored.context_text)

    async def test_parallel_fast_branch_is_saved_before_slow_branch_finishes(self):
        rid, _, first = self.fixture()
        entered = asyncio.Event()
        original = ToolExecutor._execute
        async def execute(executor, call):
            if call.call_id == "slow":
                entered.set()
                await asyncio.Event().wait()
            return await original(executor, call)
        with patch.object(ToolExecutor, "_execute", execute):
            task = asyncio.create_task(self.context(first, FixedPlanner([
                self.tool("fast"), self.tool("slow", "workspace.files.list")])))
            await asyncio.wait_for(entered.wait(), 10)
            for _ in range(100):
                with self.sessions() as db:
                    saved = db.scalar(select(AgentArtifact.id).where(AgentArtifact.run_id == first.agent_id))
                if saved:
                    break
                await asyncio.sleep(.02)
            self.assertIsNotNone(saved)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_current_tool_revocation_blocks_cached_result(self):
        rid, _, first = self.fixture()
        await self.context(first, FixedPlanner([self.tool("A")]))
        second = self.expire_and_claim(rid)
        with self.sessions() as db:
            db.add(WorkspaceToolSetting(project_id="p", tool_key="workspace.files.read", is_enabled=False))
            db.commit()
        with self.assertRaisesRegex(ValueError, "禁用|变化"):
            await self.context(second, FixedPlanner([]))

    async def test_deleted_file_cannot_be_reintroduced_by_cached_sources(self):
        rid, _, first = self.fixture()
        await self.context(first, FixedPlanner([self.tool("A")]))
        second = self.expire_and_claim(rid)
        with self.sessions() as db:
            db.delete(db.get(ProjectFile, "f"))
            db.commit()
        with self.assertRaisesRegex(ValueError, "范围已变化"):
            await self.context(second, FixedPlanner([]))

    async def test_resume_next_round_preserves_round_and_call_budgets(self):
        rid, _, first = self.fixture()
        original = first.save
        def stop_after_observe(run, node):
            original(run, node)
            if node == "plan" and run.observations:
                raise ChatLeaseLost()
        first.save = stop_after_observe
        with self.assertRaises(ChatLeaseLost):
            await self.context(first, FixedPlanner([self.tool("A")], more=True))
        second = self.expire_and_claim(rid)
        planner = FixedPlanner([self.tool("B", "workspace.files.list")])
        restored = await self.context(second, planner)
        budget = restored.diagnostics["external_tool_run_budget"]
        self.assertEqual((planner.count, budget["planning_rounds_used"], budget["tool_calls_used"]), (1, 2, 2))
        self.assertEqual(budget["replans_used"], 1)

    async def test_cancelled_inflight_result_cannot_create_artifact(self):
        rid, _, first = self.fixture()
        entered, release = asyncio.Event(), asyncio.Event()
        original = ToolExecutor._execute
        async def execute(executor, call):
            result = await original(executor, call)
            entered.set()
            await release.wait()
            return result
        with patch.object(ToolExecutor, "_execute", execute):
            task = asyncio.create_task(self.context(first, FixedPlanner([self.tool("A")])))
            await asyncio.wait_for(entered.wait(), 10)
            await asyncio.to_thread(self.call, "cancel", rid, "u")
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        with self.sessions() as db:
            self.assertIsNone(db.scalar(select(AgentArtifact.id).where(AgentArtifact.run_id == first.agent_id)))

    def test_claim_and_recovery_record_are_committed_together(self):
        rid = self.create()
        claim = self.call("claim_next", "before-worker-init")
        with self.sessions() as db:
            row = db.scalar(select(AgentRun).where(AgentRun.chat_run_id == rid))
            self.assertIsNotNone(row)
        second = self.expire_and_claim(rid)
        self.assertEqual(second.claim.version, claim.version + 1)

    async def test_current_file_scope_and_cancel_fence_reuse_and_checkpoint(self):
        rid, claim, first = self.fixture()
        await self.context(first, FixedPlanner([self.tool("A")]))
        second = self.expire_and_claim(rid)
        self.call("cancel", rid, "u")
        with self.assertRaises(ChatLeaseLost):
            await self.context(second, FixedPlanner([]))
        self.assertFalse(self.call("append", claim, [{"type": "answer_delta", "text": "迟到"}]))

    async def test_nonreadonly_inflight_call_is_not_replayed(self):
        _, _, first = self.fixture()
        with self.sessions() as db:
            executor = ToolExecutor(db=db, user_id="u", project_id="p")
            call = self.tool("write", "workspace.files.apply_edit")
            call.arguments.update(old_string="短事务", new_string="新内容")
            decision = first._begin_call(executor, call)
            self.assertEqual(decision[0], "execute")
            second = first._begin_call(executor, call)
            self.assertEqual(second[0], "blocked")
            self.assertIn("副作用", second[1])
            call.call_id = "different-id"
            self.assertEqual(first._begin_call(executor, call)[0], "blocked")

    def test_switch_to_readonly_blocks_saved_write_approval_result(self):
        _, _, persistence = self.fixture()
        with self.sessions() as db:
            executor = ToolExecutor(db=db, user_id="u", project_id="p")
            call = self.tool("write", "workspace.files.apply_edit")
            call.arguments.update(old_string="旧内容", new_string="新内容")
            decision = persistence._begin_call(executor, call)
            persistence._complete_call(decision[1], (ToolCallResult(call, "confirmation_required", [], 0), []))
        with self.sessions() as db:
            db.add(WorkspaceAgentPolicy(project_id="p", permission_mode="read_only"))
            db.commit()
        with self.sessions() as db:
            executor = ToolExecutor(db=db, user_id="u", project_id="p")
            self.assertEqual(persistence._begin_call(executor, call)[0], "blocked")

    async def test_result_binding_recovery_uses_saved_structured_upstream(self):
        rid, _, first = self.fixture()
        entered = asyncio.Event()
        counts = Counter()
        original = ToolExecutor._execute
        async def execute(executor, call):
            counts[call.call_id] += 1
            if call.call_id == "read" and counts["read"] == 1:
                entered.set()
                await asyncio.Event().wait()
            return await original(executor, call)
        downstream = self.tool("read", depends=["list"])
        downstream.arguments = {}
        downstream.result_bindings = [ToolResultBinding("list", "/sources/0/metadata/raw/files/0/file_id", "file_id")]
        with patch.object(ToolExecutor, "_execute", execute):
            task = asyncio.create_task(self.context(first, FixedPlanner([self.tool("list", "workspace.files.list"), downstream])))
            await asyncio.wait_for(entered.wait(), 10)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            second = self.expire_and_claim(rid)
            result = await self.context(second, FixedPlanner([]))
        self.assertEqual(counts, {"list": 1, "read": 2})
        self.assertIn("短事务", result.context_text)
        with self.sessions() as db:
            read = db.scalar(select(AgentStep).where(AgentStep.call_id == "1:read"))
            self.assertEqual(json.loads(read.arguments_json)["file_id"], "f")

    async def test_dependency_blocked_call_has_persistent_step_without_attempt(self):
        _, _, persistence = self.fixture()
        original = ToolExecutor._execute
        async def execute(executor, call):
            if call.call_id == "A":
                return ToolCallResult(call, "success", [], 1, quality_status="invalid"), []
            return await original(executor, call)
        class OncePlanner(FixedPlanner):
            async def plan(self, **kwargs):
                if self.count:
                    return ToolPlan("end", "test", True, False, [])
                return await super().plan(**kwargs)
        with patch.object(ToolExecutor, "_execute", execute):
            result = await self.context(persistence, OncePlanner([
                self.tool("A"), self.tool("B", "workspace.files.list", depends=["A"]),
                self.tool("C", "workspace.files.list")]))
        with self.sessions() as db:
            steps = {s.call_id: s for s in db.scalars(select(AgentStep).where(AgentStep.sequence > 0))}
            self.assertEqual((steps["1:B"].status, steps["1:B"].attempts), ("blocked", 0))
            self.assertEqual(steps["1:C"].status, "succeeded")
        self.assertTrue(result.sources)

    async def test_tool_deadline_after_crash_keeps_committed_evidence_without_new_requests(self):
        rid, _, first = self.fixture()
        entered = asyncio.Event()
        counts = Counter()
        original = ToolExecutor._execute
        async def execute(executor, call):
            counts[call.call_id] += 1
            if call.call_id == "B":
                entered.set()
                await asyncio.Event().wait()
            return await original(executor, call)
        with patch.object(ToolExecutor, "_execute", execute):
            task = asyncio.create_task(self.context(first, FixedPlanner([
                self.tool("A"), self.tool("B", "workspace.files.list", depends=["A"])])))
            await asyncio.wait_for(entered.wait(), 10)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            with self.sessions() as db:
                checkpoint = db.scalar(select(AgentCheckpoint).where(AgentCheckpoint.run_id == first.agent_id)
                    .order_by(AgentCheckpoint.state_version.desc()).limit(1))
                data = ChatToolSnapshotCodec(db, db.get(AgentRun, first.agent_id)).loads(checkpoint.planner_state_json)
                data["wall_deadline"] = (utcnow() - timedelta(seconds=1)).isoformat()
                checkpoint.planner_state_json = codec.dumps(data)
                db.commit()
            second = self.expire_and_claim(rid)
            result = await self.context(second, FixedPlanner([]))
        self.assertEqual(counts, {"A": 1, "B": 1})
        self.assertIn("短事务", result.context_text)
        self.assertEqual(result.diagnostics["external_agent_terminal_reason"], "tool_wall_clock_budget_exhausted")

    def test_generation_restart_archives_old_partial_and_resets_replay(self):
        rid, first, persistence = self.fixture()
        self.assertTrue(self.call("start_generation", first))
        self.call("append", first, [{"type": "answer_delta", "text": "旧回答半句话"}])
        original_deadline = None
        with self.sessions() as db:
            original_deadline = db.get(ChatRun, rid).deadline_at
        second = self.expire_and_claim(rid)
        self.assertTrue(self.call("start_generation", second.claim))
        self.call("append", second.claim, [{"type": "answer_delta", "text": "新回答完整句"}])
        self.call("finish", second.claim, status="succeeded")
        with self.sessions() as db:
            row = db.get(ChatRun, rid)
            self.assertEqual(row.deadline_at, original_deadline)
            self.assertEqual((row.content, row.model_attempts), ("新回答完整句", 2))
            old = db.scalar(select(AgentArtifact).where(AgentArtifact.artifact_type == "chat_generation_partial"))
            self.assertEqual(codec.loads(old.content_json)["content"], "旧回答半句话")
            events = [json.loads(e.event_json) for e in db.scalars(select(ChatRunEvent).order_by(ChatRunEvent.sequence))]
            self.assertEqual(len([e for e in events if e["type"] == "run_snapshot"]), 1)
        self.assertFalse(self.call("finish", first, status="succeeded", content="迟到覆盖"))

    def test_third_worker_loss_ends_recovery_and_user_cancel_does_not_requeue(self):
        rid, _, _ = self.fixture()
        self.expire_and_claim(rid)
        third = self.expire_and_claim(rid, "C")
        self.call("interrupt", third.claim)
        self.assertEqual(self.snapshot(rid)["status"], "interrupted")
        self.assertIsNone(self.call("claim_next", "D"))

    def test_codec_roundtrip_is_typed_and_masks_nested_secrets(self):
        source = ExternalSource("test", "test", "资料", "正常内容", metadata={"raw": {"api_key": "secret-value",
            "text": "password=abc123"}, "$type": "ToolPlan"})
        result = ToolCallResult(self.tool("a"), "success", [source], 1)
        encoded = codec.dumps((result, []))
        self.assertNotIn("secret-value", encoded)
        self.assertNotIn("abc123", encoded)
        restored = codec.loads(encoded)
        self.assertIsInstance(restored[0], ToolCallResult)
        self.assertIsInstance(restored[0].sources[0], ExternalSource)
        self.assertEqual(restored[0].sources[0].metadata["$type"], "ToolPlan")

    async def test_valid_edit_preview_is_not_persisted_as_completed_write(self):
        _, _, persistence = self.fixture()
        async def preview(executor, call):
            return ToolCallResult(call, "success", [ExternalSource("workspace_file_edit_preview", "workspace",
                "编辑预览", "仅展示Diff，文件尚未修改。")], 1, quality_status="valid", result_semantics="approval_draft"), []
        with patch.object(ToolExecutor, "_execute", preview):
            await self.context(persistence, FixedPlanner([self.tool("preview")]))
        with self.sessions() as db:
            step = db.scalar(select(AgentStep).where(AgentStep.run_id == persistence.agent_id, AgentStep.sequence > 0))
            self.assertEqual(step.status, "preview")
            self.assertEqual(db.get(ProjectFile, "f").parsed_text, "有效资料：项目采用短事务与明确审批。")

    async def test_legacy_snapshot_restores_without_repeating_completed_tools(self):
        rid, _, first = self.fixture()
        await self.context(first, FixedPlanner([self.tool("A")]))
        with self.sessions() as db:
            agent = db.get(AgentRun, first.agent_id)
            checkpoint = db.scalar(select(AgentCheckpoint).where(AgentCheckpoint.run_id == agent.id)
                .order_by(AgentCheckpoint.state_version.desc()).limit(1))
            saved = ChatToolSnapshotCodec(db, agent).loads(checkpoint.planner_state_json)
            checkpoint.planner_state_json = codec.dumps(saved)
            db.commit()
        second = self.expire_and_claim(rid)
        with patch.object(ToolExecutor, "_execute", side_effect=AssertionError("旧快照不得重做工具")):
            result = await self.context(second, FixedPlanner([]))
        self.assertIn("短事务", result.context_text)
