import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.agent_runtime import (
    AgentApprovalResponse,
    AgentArtifactResponse,
    AgentOutboxEventResponse,
    AgentRunResponse,
    AgentRunSnapshotResponse,
    AgentStepResponse,
    ApprovalApplyRequest,
    ApprovalChallengeResponse,
    DurableToolRunRequest,
    FileEditApplyResponse,
    FileEditProposalResponse,
    FileRevisionResponse,
    FileRevisionRestoreRequest,
    PatchDraftResponse,
)
from app.services.durable_tool_runtime import DurableToolRunService, DurableToolRuntimeError
from app.services.agent_runtime_service import AgentRuntimeError, AgentRuntimeService


router = APIRouter(prefix="/agent-runtime", tags=["agent-runtime"])


def _http_error(exc: AgentRuntimeError) -> HTTPException:
    not_found = {
        "approval_not_found",
        "run_not_found",
        "draft_missing",
        "file_not_found",
        "conversation_not_found",
        "assistant_message_not_found",
    }
    code = status.HTTP_404_NOT_FOUND if exc.code in not_found else status.HTTP_409_CONFLICT
    return HTTPException(status_code=code, detail={"code": exc.code, "message": str(exc)})


def _durable_http_error(exc: DurableToolRuntimeError) -> HTTPException:
    not_found = {
        "unknown_tool",
        "project_not_found",
        "conversation_not_found",
        "assistant_message_not_found",
    }
    code = status.HTTP_404_NOT_FOUND if exc.code in not_found else status.HTTP_409_CONFLICT
    return HTTPException(status_code=code, detail={"code": exc.code, "message": str(exc)})


@router.get("/runs", response_model=list[AgentRunResponse])
def list_agent_runs(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[AgentRunResponse]:
    return [
        AgentRunResponse.model_validate(run)
        for run in AgentRuntimeService(db).list_runs(user_id=current_user.id)
    ]


@router.get("/runs/{run_id}", response_model=AgentRunSnapshotResponse)
def get_agent_run(
    run_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AgentRunSnapshotResponse:
    snapshot = DurableToolRunService(db).get_run_snapshot(run_id=run_id, user_id=current_user.id)
    if snapshot is None:
        snapshot = AgentRuntimeService(db).get_run_snapshot(run_id=run_id, user_id=current_user.id)
    if not snapshot:
        raise HTTPException(status_code=404, detail="Agent Run not found")
    checkpoint = snapshot["checkpoint"]
    checkpoint_payload = None
    if checkpoint:
        checkpoint_payload = {
            "id": checkpoint.id,
            "step_sequence": checkpoint.step_sequence,
            "state_version": checkpoint.state_version,
            "planner_state": json.loads(checkpoint.planner_state_json or "{}"),
            "observations": json.loads(checkpoint.observations_json or "[]"),
            "remaining_budget": json.loads(checkpoint.remaining_budget_json or "{}"),
            "created_at": checkpoint.created_at,
        }
    return AgentRunSnapshotResponse(
        run=AgentRunResponse.model_validate(snapshot["run"]),
        steps=[AgentStepResponse.model_validate(item) for item in snapshot["steps"]],
        checkpoint=checkpoint_payload,
        approvals=[AgentApprovalResponse.model_validate(item) for item in snapshot.get("approvals", [])],
        drafts=[PatchDraftResponse.model_validate(item) for item in snapshot.get("drafts", [])],
        artifacts=[AgentArtifactResponse.model_validate(item) for item in snapshot.get("artifacts", [])],
        outbox_events=[AgentOutboxEventResponse.model_validate(item) for item in snapshot.get("outbox_events", [])],
    )


@router.post("/tool-runs", response_model=AgentRunResponse, status_code=status.HTTP_202_ACCEPTED)
def enqueue_durable_tool_run(
    payload: DurableToolRunRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AgentRunResponse:
    try:
        run = DurableToolRunService(db).enqueue(
            user_id=current_user.id,
            project_id=payload.project_id,
            conversation_id=payload.conversation_id,
            assistant_message_id=payload.assistant_message_id,
            idempotency_key=payload.idempotency_key,
            skill_key=payload.skill_key,
            max_attempts=payload.max_attempts,
            calls=[call.model_dump() for call in payload.calls],
        )
    except DurableToolRuntimeError as exc:
        raise _durable_http_error(exc) from exc
    return AgentRunResponse.model_validate(run)


@router.post("/tool-runs/{run_id}/steps/{step_id}/replay", response_model=AgentRunResponse)
def replay_durable_dead_letter(
    run_id: str,
    step_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> AgentRunResponse:
    try:
        run = DurableToolRunService(db).replay_dead_letter(
            run_id=run_id,
            step_id=step_id,
            user_id=current_user.id,
        )
    except DurableToolRuntimeError as exc:
        raise _durable_http_error(exc) from exc
    return AgentRunResponse.model_validate(run)


@router.post("/tool-runs/reconcile")
def reconcile_durable_tool_runs(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, int]:
    return {"restored_steps": DurableToolRunService(db).reconcile_orphaned_steps(user_id=current_user.id)}


@router.get("/metrics")
def get_agent_runtime_metrics(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    return DurableToolRunService(db).metrics(user_id=current_user.id)


@router.get("/files/{file_id}/revisions", response_model=list[FileRevisionResponse])
def list_file_revisions(
    file_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[FileRevisionResponse]:
    try:
        revisions = AgentRuntimeService(db).list_file_revisions(file_id=file_id, user_id=current_user.id)
    except AgentRuntimeError as exc:
        raise _http_error(exc) from exc
    return [FileRevisionResponse.model_validate(item) for item in revisions]


@router.post(
    "/files/{file_id}/revisions/{revision_id}/restore",
    response_model=FileEditProposalResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def propose_file_revision_restore(
    file_id: str,
    revision_id: str,
    payload: FileRevisionRestoreRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> FileEditProposalResponse:
    try:
        proposal = AgentRuntimeService(db).propose_file_restore(
            file_id=file_id,
            revision_id=revision_id,
            user_id=current_user.id,
            conversation_id=payload.conversation_id,
            assistant_message_id=payload.assistant_message_id,
        )
    except AgentRuntimeError as exc:
        raise _http_error(exc) from exc
    return FileEditProposalResponse(**proposal.__dict__)


@router.post(
    "/approvals/{approval_id}/challenge",
    response_model=ApprovalChallengeResponse,
)
def issue_approval_challenge(
    approval_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ApprovalChallengeResponse:
    try:
        token = AgentRuntimeService(db).issue_approval_challenge(
            approval_id=approval_id,
            user_id=current_user.id,
        )
    except AgentRuntimeError as exc:
        raise _http_error(exc) from exc
    # token 只在这一次响应中返回；数据库和 Tool Trace 只保存 SHA-256。
    return ApprovalChallengeResponse(approval_id=approval_id, approval_token=token)


@router.post("/approvals/{approval_id}/apply", response_model=FileEditApplyResponse)
def apply_approval(
    approval_id: str,
    payload: ApprovalApplyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> FileEditApplyResponse:
    try:
        result = AgentRuntimeService(db).apply_approved_file_edit(
            approval_id=approval_id,
            user_id=current_user.id,
            approval_token=payload.approval_token,
        )
    except AgentRuntimeError as exc:
        raise _http_error(exc) from exc
    return FileEditApplyResponse(**result.__dict__)


@router.post("/approvals/{approval_id}/reject", response_model=FileEditApplyResponse)
def reject_approval(
    approval_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> FileEditApplyResponse:
    try:
        result = AgentRuntimeService(db).reject_approval(
            approval_id=approval_id,
            user_id=current_user.id,
        )
    except AgentRuntimeError as exc:
        raise _http_error(exc) from exc
    return FileEditApplyResponse(**result.__dict__)
