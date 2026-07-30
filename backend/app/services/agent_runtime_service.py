from __future__ import annotations

import difflib
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.agent_runtime import (
    AgentApproval,
    AgentCheckpoint,
    AgentRun,
    AgentStep,
    FileRevision,
    PatchDraft,
)
from app.models.project_file import ProjectFile


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AgentRuntimeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class FileEditProposal:
    run_id: str
    step_id: str
    patch_draft_id: str
    approval_id: str
    file_id: str
    file_name: str
    diff_text: str
    arguments_hash: str
    expires_at: datetime


@dataclass(frozen=True)
class FileEditApplyResult:
    run_id: str
    step_id: str
    patch_draft_id: str
    approval_id: str
    file_id: str
    revision_id: str | None
    revision_number: int | None
    status: str


class AgentRuntimeService:
    MAX_DIFF_CHARS = 20_000

    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def _hash_text(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @classmethod
    def _arguments_hash(
        cls,
        *,
        user_id: str,
        project_id: str,
        file_id: str,
        old_string: str,
        new_string: str,
    ) -> str:
        payload = json.dumps(
            {
                "user_id": user_id,
                "project_id": project_id,
                "tool_key": "workspace.files.apply_edit",
                "file_id": file_id,
                "old_string": old_string,
                "new_string": new_string,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return cls._hash_text(payload)

    def propose_file_edit(
        self,
        *,
        user_id: str,
        project_id: str | None,
        call_id: str,
        file_id: str,
        old_string: str,
        new_string: str,
        depends_on: list[str] | None = None,
        conversation_id: str | None = None,
        assistant_message_id: str | None = None,
    ) -> FileEditProposal:
        if not project_id:
            raise AgentRuntimeError("project_required", "文件写入必须关联当前工作区。")
        if not file_id or not old_string:
            raise AgentRuntimeError("invalid_arguments", "file_id 和非空 old_string 为必填项。")
        project_file = self.db.scalars(
            select(ProjectFile)
            .where(
                ProjectFile.id == file_id,
                ProjectFile.user_id == user_id,
                ProjectFile.project_id == project_id,
            )
            .limit(1)
        ).first()
        if not project_file:
            raise AgentRuntimeError("file_not_found", "工作区中未找到该文件。")

        original = project_file.parsed_text or ""
        matches = original.count(old_string)
        if matches == 0:
            raise AgentRuntimeError("stale_edit", "old_string 未在当前版本中找到，请重新读取文件。")
        if matches > 1:
            raise AgentRuntimeError("ambiguous_edit", f"old_string 出现 {matches} 次，请提供更多上下文。")
        updated = original.replace(old_string, new_string, 1)
        arguments_hash = self._arguments_hash(
            user_id=user_id,
            project_id=project_id,
            file_id=file_id,
            old_string=old_string,
            new_string=new_string,
        )
        # call_id 由 Planner 生成，跨请求重试时可能变化，不能作为幂等主键。
        # 参数哈希 + 当前基线内容哈希把“同一版本上的同一修改”稳定绑定在一起；
        # 文件版本变化后则允许生成新的冲突修复提案。
        idempotency_key = f"file-edit:{user_id}:{arguments_hash}:{self._hash_text(original)}"
        existing_run = self.db.scalars(
            select(AgentRun).where(AgentRun.idempotency_key == idempotency_key).limit(1)
        ).first()
        if existing_run:
            return self._proposal_for_run(existing_run, user_id)

        base_revision = self._ensure_current_revision(project_file)
        diff_text = "\n".join(
            difflib.unified_diff(
                original.splitlines(),
                updated.splitlines(),
                fromfile=f"a/{project_file.file_name}",
                tofile=f"b/{project_file.file_name}",
                lineterm="",
                n=3,
            )
        )
        if len(diff_text) > self.MAX_DIFF_CHARS:
            diff_text = diff_text[: self.MAX_DIFF_CHARS].rstrip() + "\n[Diff 已截断；应用时仍校验完整内容哈希]"
        expires_at = utcnow() + timedelta(minutes=30)
        run = AgentRun(
            user_id=user_id,
            project_id=project_id,
            conversation_id=conversation_id,
            assistant_message_id=assistant_message_id,
            status="waiting_approval",
            input_json=json.dumps({"file_id": file_id, "tool_key": "workspace.files.apply_edit"}),
            planner_state_json=json.dumps({"call_id": call_id, "depends_on": depends_on or []}),
            idempotency_key=idempotency_key,
            state_version=1,
            current_step=1,
        )
        self.db.add(run)
        self.db.flush()
        step = AgentStep(
            run_id=run.id,
            sequence=1,
            call_id=call_id,
            tool_key="workspace.files.apply_edit",
            arguments_json=json.dumps(
                {"file_id": file_id, "old_string": old_string, "new_string": new_string},
                ensure_ascii=False,
            ),
            arguments_hash=arguments_hash,
            status="waiting_approval",
            depends_on_json=json.dumps(depends_on or []),
        )
        self.db.add(step)
        self.db.flush()
        draft = PatchDraft(
            run_id=run.id,
            step_id=step.id,
            project_file_id=file_id,
            base_revision_id=base_revision.id,
            base_content_hash=base_revision.content_hash,
            old_string=old_string,
            new_string=new_string,
            proposed_content_hash=self._hash_text(updated),
            diff_text=diff_text,
            arguments_hash=arguments_hash,
            status="proposed",
            expires_at=expires_at,
        )
        approval = AgentApproval(
            run_id=run.id,
            step_id=step.id,
            user_id=user_id,
            action="workspace.files.apply_edit",
            arguments_hash=arguments_hash,
            status="pending",
            expires_at=expires_at,
        )
        self.db.add_all([draft, approval])
        self.db.flush()
        self.db.add(
            AgentCheckpoint(
                run_id=run.id,
                step_sequence=1,
                state_version=run.state_version,
                planner_state_json=run.planner_state_json,
                observations_json=json.dumps(
                    [{"type": "approval_required", "approval_id": approval.id, "patch_draft_id": draft.id}]
                ),
                remaining_budget_json=json.dumps({"remaining_steps": run.max_steps - 1}),
            )
        )
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            existing_run = self.db.scalars(
                select(AgentRun).where(AgentRun.idempotency_key == idempotency_key).limit(1)
            ).first()
            if not existing_run:
                raise
            return self._proposal_for_run(existing_run, user_id)
        return FileEditProposal(
            run_id=run.id,
            step_id=step.id,
            patch_draft_id=draft.id,
            approval_id=approval.id,
            file_id=file_id,
            file_name=project_file.file_name,
            diff_text=diff_text,
            arguments_hash=arguments_hash,
            expires_at=expires_at,
        )

    def issue_approval_challenge(self, *, approval_id: str, user_id: str) -> str:
        approval = self.db.scalars(
            select(AgentApproval)
            .where(AgentApproval.id == approval_id, AgentApproval.user_id == user_id)
            .with_for_update()
        ).first()
        if not approval:
            raise AgentRuntimeError("approval_not_found", "审批不存在。")
        if approval.status == "consumed":
            raise AgentRuntimeError("already_consumed", "该审批已完成，无需重复获取确认挑战。")
        if approval.status != "pending" or self._as_utc(approval.expires_at) <= utcnow():
            approval.status = "expired"
            self.db.commit()
            raise AgentRuntimeError("approval_expired", "审批已失效，请重新生成编辑提案。")
        token = secrets.token_urlsafe(32)
        approval.nonce_hash = self._hash_text(token)
        self.db.commit()
        return token

    def apply_trusted_workspace_file_edit(
        self,
        *,
        proposal: FileEditProposal,
        user_id: str,
    ) -> FileEditApplyResult:
        """Auto-apply an allowlisted workspace edit while preserving CAS and audit state.

        This is intentionally not a generic approval bypass. The caller must already
        have resolved the workspace policy, and only the scoped ProjectFile edit uses it.
        """

        approval = self.db.scalars(
            select(AgentApproval)
            .where(AgentApproval.id == proposal.approval_id, AgentApproval.user_id == user_id)
            .with_for_update()
        ).first()
        if not approval:
            raise AgentRuntimeError("approval_not_found", "审批不存在。")
        if approval.status == "consumed":
            return self.apply_approved_file_edit(
                approval_id=proposal.approval_id,
                user_id=user_id,
                approval_token="",
            )
        if approval.status != "pending":
            raise AgentRuntimeError("approval_not_pending", "编辑提案当前不可自动应用。")
        approval.decision_mode = "full_workspace_policy"
        token = secrets.token_urlsafe(32)
        approval.nonce_hash = self._hash_text(token)
        self.db.commit()
        return self.apply_approved_file_edit(
            approval_id=proposal.approval_id,
            user_id=user_id,
            approval_token=token,
        )

    def apply_approved_file_edit(
        self,
        *,
        approval_id: str,
        user_id: str,
        approval_token: str,
    ) -> FileEditApplyResult:
        approval = self.db.scalars(
            select(AgentApproval)
            .where(AgentApproval.id == approval_id, AgentApproval.user_id == user_id)
            .with_for_update()
        ).first()
        if not approval:
            raise AgentRuntimeError("approval_not_found", "审批不存在。")
        draft = self.db.scalars(select(PatchDraft).where(PatchDraft.step_id == approval.step_id)).first()
        if not draft:
            raise AgentRuntimeError("draft_missing", "编辑提案不存在。")
        if approval.status == "consumed" and draft.status == "applied":
            revision = self.db.scalars(
                select(FileRevision)
                .where(
                    FileRevision.source_step_id == approval.step_id,
                    FileRevision.project_file_id == draft.project_file_id,
                )
                .limit(1)
            ).first()
            return self._apply_result(approval, draft, revision, status="applied")
        if approval.status != "pending" or self._as_utc(approval.expires_at) <= utcnow():
            approval.status = "expired"
            draft.status = "expired"
            self.db.commit()
            raise AgentRuntimeError("approval_expired", "审批已失效，请重新生成编辑提案。")
        token_hash = self._hash_text(approval_token or "")
        if not approval.nonce_hash or not hmac.compare_digest(token_hash, approval.nonce_hash):
            raise AgentRuntimeError("invalid_approval_token", "确认挑战不匹配，请重新获取。")

        run = self.db.scalars(select(AgentRun).where(AgentRun.id == approval.run_id).with_for_update()).first()
        step = self.db.scalars(select(AgentStep).where(AgentStep.id == approval.step_id).with_for_update()).first()
        project_file = self.db.scalars(
            select(ProjectFile)
            .where(ProjectFile.id == draft.project_file_id, ProjectFile.user_id == user_id)
            .with_for_update()
        ).first()
        if not run or not step or not project_file:
            raise AgentRuntimeError("runtime_state_missing", "Agent 恢复状态不完整，拒绝写入。")
        if approval.arguments_hash != step.arguments_hash or approval.arguments_hash != draft.arguments_hash:
            raise AgentRuntimeError("arguments_changed", "审批参数哈希不一致，拒绝写入。")
        current = project_file.parsed_text or ""
        if self._hash_text(current) != draft.base_content_hash:
            run.status = "conflict"
            step.status = "conflict"
            step.error_code = "file_revision_conflict"
            step.error_message = "文件在审批期间已发生变化，未覆盖新版本。"
            draft.status = "conflict"
            approval.status = "expired"
            run.state_version += 1
            self._checkpoint(run, step, [{"type": "file_revision_conflict", "draft_id": draft.id}])
            self.db.commit()
            raise AgentRuntimeError("file_revision_conflict", "文件已变化，请重新读取并生成 Diff。")
        if current.count(draft.old_string) != 1:
            raise AgentRuntimeError("edit_no_longer_unique", "old_string 已不再唯一，拒绝写入。")
        updated = current.replace(draft.old_string, draft.new_string, 1)
        if self._hash_text(updated) != draft.proposed_content_hash:
            raise AgentRuntimeError("proposed_hash_mismatch", "提案内容哈希不一致，拒绝写入。")

        revision_number = int(
            self.db.scalar(
                select(func.coalesce(func.max(FileRevision.revision_number), 0)).where(
                    FileRevision.project_file_id == project_file.id
                )
            )
            or 0
        ) + 1
        project_file.parsed_text = updated
        revision = FileRevision(
            project_file_id=project_file.id,
            revision_number=revision_number,
            content_hash=draft.proposed_content_hash,
            parsed_text=updated,
            created_by=(
                "agent_trusted_workspace_edit"
                if approval.decision_mode == "full_workspace_policy"
                else "agent_approved_edit"
            ),
            source_run_id=run.id,
            source_step_id=step.id,
        )
        self.db.add(revision)
        draft.status = "applied"
        draft.applied_at = utcnow()
        approval.status = "consumed"
        approval.consumed_at = utcnow()
        approval.nonce_hash = None
        step.status = "succeeded"
        step.attempts = (step.attempts or 0) + 1
        step.result_json = json.dumps(
            {"file_id": project_file.id, "revision_number": revision_number, "content_hash": revision.content_hash}
        )
        step.finished_at = utcnow()
        run.status = "succeeded"
        run.finished_at = utcnow()
        run.state_version += 1
        self.db.flush()
        self._checkpoint(
            run,
            step,
            [{"type": "file_edit_applied", "revision_id": revision.id, "revision_number": revision_number}],
        )
        self.db.commit()
        return self._apply_result(approval, draft, revision, status="applied")

    def reject_approval(self, *, approval_id: str, user_id: str) -> FileEditApplyResult:
        approval = self.db.scalars(
            select(AgentApproval)
            .where(AgentApproval.id == approval_id, AgentApproval.user_id == user_id)
            .with_for_update()
        ).first()
        if not approval:
            raise AgentRuntimeError("approval_not_found", "审批不存在。")
        draft = self.db.scalars(select(PatchDraft).where(PatchDraft.step_id == approval.step_id)).first()
        step = self.db.get(AgentStep, approval.step_id)
        run = self.db.get(AgentRun, approval.run_id)
        if approval.status == "consumed":
            raise AgentRuntimeError("already_consumed", "已应用的编辑不能再拒绝。")
        approval.status = "rejected"
        approval.nonce_hash = None
        if draft:
            draft.status = "rejected"
        if step:
            step.status = "cancelled"
            step.finished_at = utcnow()
        if run:
            run.status = "cancelled"
            run.finished_at = utcnow()
            run.state_version += 1
            self._checkpoint(run, step, [{"type": "approval_rejected"}])
        self.db.commit()
        if not draft:
            raise AgentRuntimeError("draft_missing", "编辑提案不存在。")
        return self._apply_result(approval, draft, None, status="rejected")

    def list_runs(self, *, user_id: str, limit: int = 30) -> list[AgentRun]:
        return list(
            self.db.scalars(
                select(AgentRun)
                .where(AgentRun.user_id == user_id)
                .order_by(AgentRun.created_at.desc())
                .limit(limit)
            ).all()
        )

    def get_run_snapshot(self, *, run_id: str, user_id: str) -> dict[str, Any] | None:
        run = self.db.scalars(
            select(AgentRun).where(AgentRun.id == run_id, AgentRun.user_id == user_id).limit(1)
        ).first()
        if not run:
            return None
        steps = list(self.db.scalars(select(AgentStep).where(AgentStep.run_id == run.id).order_by(AgentStep.sequence)).all())
        checkpoint = self.db.scalars(
            select(AgentCheckpoint)
            .where(AgentCheckpoint.run_id == run.id)
            .order_by(AgentCheckpoint.state_version.desc())
            .limit(1)
        ).first()
        approvals = list(self.db.scalars(select(AgentApproval).where(AgentApproval.run_id == run.id)).all())
        drafts = list(self.db.scalars(select(PatchDraft).where(PatchDraft.run_id == run.id)).all())
        return {"run": run, "steps": steps, "checkpoint": checkpoint, "approvals": approvals, "drafts": drafts}

    def _ensure_current_revision(self, project_file: ProjectFile) -> FileRevision:
        current = project_file.parsed_text or ""
        current_hash = self._hash_text(current)
        latest = self.db.scalars(
            select(FileRevision)
            .where(FileRevision.project_file_id == project_file.id)
            .order_by(FileRevision.revision_number.desc())
            .limit(1)
        ).first()
        if latest and latest.content_hash == current_hash:
            return latest
        revision = FileRevision(
            project_file_id=project_file.id,
            revision_number=(latest.revision_number + 1) if latest else 1,
            content_hash=current_hash,
            parsed_text=current,
            created_by="baseline_sync" if latest else "baseline",
        )
        self.db.add(revision)
        self.db.flush()
        return revision

    def _proposal_for_run(self, run: AgentRun, user_id: str) -> FileEditProposal:
        if run.user_id != user_id:
            raise AgentRuntimeError("run_not_found", "Agent Run 不存在。")
        step = self.db.scalars(select(AgentStep).where(AgentStep.run_id == run.id).limit(1)).first()
        draft = self.db.scalars(select(PatchDraft).where(PatchDraft.run_id == run.id).limit(1)).first()
        approval = self.db.scalars(select(AgentApproval).where(AgentApproval.run_id == run.id).limit(1)).first()
        project_file = self.db.get(ProjectFile, draft.project_file_id) if draft else None
        if not step or not draft or not approval or not project_file:
            raise AgentRuntimeError("runtime_state_missing", "Agent Run 状态不完整。")
        return FileEditProposal(
            run_id=run.id,
            step_id=step.id,
            patch_draft_id=draft.id,
            approval_id=approval.id,
            file_id=draft.project_file_id,
            file_name=project_file.file_name,
            diff_text=draft.diff_text,
            arguments_hash=draft.arguments_hash,
            expires_at=draft.expires_at,
        )

    def _checkpoint(self, run: AgentRun, step: AgentStep | None, observations: list[dict[str, Any]]) -> None:
        self.db.add(
            AgentCheckpoint(
                run_id=run.id,
                step_sequence=step.sequence if step else run.current_step,
                state_version=run.state_version,
                planner_state_json=run.planner_state_json,
                observations_json=json.dumps(observations, ensure_ascii=False),
                remaining_budget_json=json.dumps({"remaining_steps": max(0, run.max_steps - run.current_step)}),
            )
        )

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)

    @staticmethod
    def _apply_result(
        approval: AgentApproval,
        draft: PatchDraft,
        revision: FileRevision | None,
        *,
        status: str,
    ) -> FileEditApplyResult:
        return FileEditApplyResult(
            run_id=approval.run_id,
            step_id=approval.step_id,
            patch_draft_id=draft.id,
            approval_id=approval.id,
            file_id=draft.project_file_id,
            revision_id=revision.id if revision else None,
            revision_number=revision.revision_number if revision else None,
            status=status,
        )
