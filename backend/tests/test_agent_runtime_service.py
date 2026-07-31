from __future__ import annotations

import unittest
import asyncio

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.core.database import Base
from app.models.agent_runtime import AgentApproval, AgentCheckpoint, AgentRun, AgentStep, FileRevision, PatchDraft
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.project import Project
from app.models.project_file import ProjectFile
from app.models.tool_config import WorkspaceAgentPolicy
from app.models.user import User
from app.services.agent_runtime_service import AgentRuntimeError, AgentRuntimeService
from app.services.tools.executor import ToolExecutor
from app.services.tools.schemas import PlannedToolCall


class AgentRuntimeServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite+pysqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.db = Session(self.engine)
        self.user = User(username="owner", email="owner@example.test")
        self.other = User(username="other", email="other@example.test")
        self.db.add_all([self.user, self.other])
        self.db.flush()
        self.project = Project(user_id=self.user.id, name="workspace")
        self.db.add(self.project)
        self.db.flush()
        self.file = ProjectFile(
            project_id=self.project.id,
            user_id=self.user.id,
            kind="text",
            file_name="notes.md",
            storage_key="owner/notes.md",
            parsed_text="title\nold value\nend",
        )
        self.db.add(self.file)
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()
        self.engine.dispose()

    def _propose(self):
        return AgentRuntimeService(self.db).propose_file_edit(
            user_id=self.user.id,
            project_id=self.project.id,
            call_id="call-1",
            file_id=self.file.id,
            old_string="old value",
            new_string="new value",
        )

    def test_proposal_persists_full_checkpoint_without_mutating_file(self) -> None:
        proposal = self._propose()

        self.db.refresh(self.file)
        self.assertEqual(self.file.parsed_text, "title\nold value\nend")
        self.assertIn("-old value", proposal.diff_text)
        self.assertIn("+new value", proposal.diff_text)
        self.assertEqual(self.db.scalar(select(AgentRun)).status, "waiting_approval")
        self.assertEqual(self.db.scalar(select(AgentStep)).status, "waiting_approval")
        self.assertEqual(self.db.scalar(select(PatchDraft)).status, "proposed")
        self.assertEqual(self.db.scalar(select(AgentApproval)).status, "pending")
        self.assertEqual(self.db.scalar(select(AgentCheckpoint)).state_version, 1)
        self.assertEqual(self.db.scalar(select(FileRevision)).revision_number, 1)

        duplicate = self._propose()
        self.assertEqual(duplicate.run_id, proposal.run_id)
        self.assertEqual(self.db.query(AgentRun).count(), 1)

    def test_approval_token_and_revision_cas_make_apply_idempotent(self) -> None:
        proposal = self._propose()
        service = AgentRuntimeService(self.db)
        token = service.issue_approval_challenge(
            approval_id=proposal.approval_id,
            user_id=self.user.id,
        )

        with self.assertRaisesRegex(AgentRuntimeError, "确认挑战不匹配"):
            service.apply_approved_file_edit(
                approval_id=proposal.approval_id,
                user_id=self.user.id,
                approval_token="x" * 32,
            )

        applied = service.apply_approved_file_edit(
            approval_id=proposal.approval_id,
            user_id=self.user.id,
            approval_token=token,
        )
        self.assertEqual(applied.status, "applied")
        self.assertEqual(applied.revision_number, 2)
        self.db.refresh(self.file)
        self.assertEqual(self.file.parsed_text, "title\nnew value\nend")

        repeated = service.apply_approved_file_edit(
            approval_id=proposal.approval_id,
            user_id=self.user.id,
            approval_token="already-consumed-does-not-reapply",
        )
        self.assertEqual(repeated.revision_number, 2)
        self.assertEqual(self.db.query(FileRevision).count(), 2)

    def test_file_change_between_preview_and_approval_fails_closed(self) -> None:
        proposal = self._propose()
        service = AgentRuntimeService(self.db)
        token = service.issue_approval_challenge(approval_id=proposal.approval_id, user_id=self.user.id)
        self.file.parsed_text = "title\nconcurrent change\nend"
        self.db.commit()

        with self.assertRaisesRegex(AgentRuntimeError, "重新读取"):
            service.apply_approved_file_edit(
                approval_id=proposal.approval_id,
                user_id=self.user.id,
                approval_token=token,
            )
        self.db.refresh(self.file)
        self.assertEqual(self.file.parsed_text, "title\nconcurrent change\nend")
        self.assertEqual(self.db.get(AgentRun, proposal.run_id).status, "conflict")

    def test_other_user_cannot_issue_challenge(self) -> None:
        proposal = self._propose()
        with self.assertRaisesRegex(AgentRuntimeError, "审批不存在"):
            AgentRuntimeService(self.db).issue_approval_challenge(
                approval_id=proposal.approval_id,
                user_id=self.other.id,
            )

    def test_executor_returns_waiting_diff_without_unlocking_direct_write(self) -> None:
        call = PlannedToolCall(
            call_id="executor-call",
            tool_key="workspace.files.apply_edit",
            provider="workspace",
            category="workspace_file",
            display_name="工作区文件受控修改",
            confidence=0.9,
            reason="user requested edit",
            arguments={"file_id": self.file.id, "old_string": "old value", "new_string": "new value"},
        )
        result, events = asyncio.run(
            ToolExecutor(
                db=self.db,
                user_id=self.user.id,
                project_id=self.project.id,
            ).execute(call)
        )

        self.assertEqual(result.status, "confirmation_required")
        self.assertEqual(len(result.sources), 1)
        self.assertIn("尚未写入", result.sources[0].display_text)
        self.assertIn("tool_confirmation_required", [event.type for event in events])
        self.db.refresh(self.file)
        self.assertIn("old value", self.file.parsed_text)

    def test_full_workspace_policy_auto_applies_only_scoped_file_edit(self) -> None:
        self.db.add(
            WorkspaceAgentPolicy(
                project_id=self.project.id,
                permission_mode="full_workspace",
            )
        )
        self.db.commit()
        call = PlannedToolCall(
            call_id="trusted-executor-call",
            tool_key="workspace.files.apply_edit",
            provider="workspace",
            category="workspace_file",
            display_name="工作区文件受控修改",
            confidence=0.9,
            reason="user selected full workspace access",
            arguments={"file_id": self.file.id, "old_string": "old value", "new_string": "new value"},
        )

        result, events = asyncio.run(
            ToolExecutor(
                db=self.db,
                user_id=self.user.id,
                project_id=self.project.id,
            ).execute(call)
        )

        self.assertEqual(result.status, "success")
        self.assertTrue(result.sources[0].metadata["applied"])
        self.db.refresh(self.file)
        self.assertIn("new value", self.file.parsed_text)
        approval = self.db.scalar(select(AgentApproval))
        self.assertEqual(approval.status, "consumed")
        self.assertEqual(approval.decision_mode, "full_workspace_policy")
        latest_revision = self.db.scalars(
            select(FileRevision).order_by(FileRevision.revision_number.desc()).limit(1)
        ).first()
        self.assertEqual(latest_revision.created_by, "agent_trusted_workspace_edit")
        self.assertEqual(events[-1].payload["permission_mode"], "full_workspace")

    def test_read_only_policy_blocks_file_edit_before_creating_runtime_state(self) -> None:
        self.db.add(
            WorkspaceAgentPolicy(
                project_id=self.project.id,
                permission_mode="read_only",
            )
        )
        self.db.commit()
        call = PlannedToolCall(
            call_id="readonly-executor-call",
            tool_key="workspace.files.apply_edit",
            provider="workspace",
            category="workspace_file",
            display_name="工作区文件受控修改",
            confidence=0.9,
            reason="test read only policy",
            arguments={"file_id": self.file.id, "old_string": "old value", "new_string": "new value"},
        )

        result, events = asyncio.run(
            ToolExecutor(
                db=self.db,
                user_id=self.user.id,
                project_id=self.project.id,
            ).execute(call)
        )

        self.assertEqual(result.status, "skipped")
        self.assertEqual(self.db.query(AgentRun).count(), 0)
        self.assertTrue(any(event.payload.get("permission_mode") == "read_only" for event in events))

    def test_revision_restore_is_an_approval_proposal_not_a_direct_write(self) -> None:
        service = AgentRuntimeService(self.db)
        proposal = self._propose()
        token = service.issue_approval_challenge(approval_id=proposal.approval_id, user_id=self.user.id)
        service.apply_approved_file_edit(
            approval_id=proposal.approval_id,
            user_id=self.user.id,
            approval_token=token,
        )
        revisions = service.list_file_revisions(file_id=self.file.id, user_id=self.user.id)
        baseline = revisions[-1]

        restore = service.propose_file_restore(
            file_id=self.file.id,
            revision_id=baseline.id,
            user_id=self.user.id,
        )
        self.db.refresh(self.file)
        self.assertIn("new value", self.file.parsed_text)
        self.assertIn("-new value", restore.diff_text)
        restore_token = service.issue_approval_challenge(
            approval_id=restore.approval_id,
            user_id=self.user.id,
        )
        restored = service.apply_approved_file_edit(
            approval_id=restore.approval_id,
            user_id=self.user.id,
            approval_token=restore_token,
        )
        self.db.refresh(self.file)
        self.assertEqual(restored.revision_number, 3)
        self.assertEqual(self.file.parsed_text, "title\nold value\nend")

    def test_empty_file_can_restore_a_non_empty_revision(self) -> None:
        service = AgentRuntimeService(self.db)
        baseline = service._ensure_current_revision(self.file)
        self.db.commit()
        self.file.parsed_text = ""
        self.db.commit()

        restore = service.propose_file_restore(
            file_id=self.file.id,
            revision_id=baseline.id,
            user_id=self.user.id,
        )
        self.db.refresh(self.file)
        self.assertEqual(self.file.parsed_text, "")
        token = service.issue_approval_challenge(
            approval_id=restore.approval_id,
            user_id=self.user.id,
        )
        applied = service.apply_approved_file_edit(
            approval_id=restore.approval_id,
            user_id=self.user.id,
            approval_token=token,
        )
        self.db.refresh(self.file)
        self.assertEqual(applied.status, "applied")
        self.assertEqual(self.file.parsed_text, "title\nold value\nend")

    def test_file_proposal_rejects_foreign_conversation_context(self) -> None:
        foreign_project = Project(user_id=self.other.id, name="foreign-workspace")
        self.db.add(foreign_project)
        self.db.flush()
        foreign_conversation = Conversation(
            user_id=self.other.id,
            project_id=foreign_project.id,
            title="foreign-conversation",
            model_name="test-model",
        )
        self.db.add(foreign_conversation)
        self.db.flush()
        foreign_message = Message(
            conversation_id=foreign_conversation.id,
            sequence=1,
            role="assistant",
            content="",
            status="done",
        )
        self.db.add(foreign_message)
        self.db.commit()

        with self.assertRaisesRegex(AgentRuntimeError, "未找到该会话"):
            AgentRuntimeService(self.db).propose_file_edit(
                user_id=self.user.id,
                project_id=self.project.id,
                call_id="cross-tenant-context",
                file_id=self.file.id,
                old_string="old value",
                new_string="new value",
                conversation_id=foreign_conversation.id,
                assistant_message_id=foreign_message.id,
            )
        self.assertEqual(self.db.query(AgentRun).count(), 0)


if __name__ == "__main__":
    unittest.main()
