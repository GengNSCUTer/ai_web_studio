import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.schemas.agent_runtime import (
    AgentApprovalResponse,
    AgentRunResponse,
    AgentRunSnapshotResponse,
    AgentStepResponse,
    ApprovalApplyRequest,
    ApprovalChallengeResponse,
    FileEditApplyResponse,
    PatchDraftResponse,
)
from app.services.agent_runtime_service import AgentRuntimeError, AgentRuntimeService


router = APIRouter(prefix="/agent-runtime", tags=["agent-runtime"])


def _http_error(exc: AgentRuntimeError) -> HTTPException:
    not_found = {"approval_not_found", "run_not_found", "draft_missing"}
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
        approvals=[AgentApprovalResponse.model_validate(item) for item in snapshot["approvals"]],
        drafts=[PatchDraftResponse.model_validate(item) for item in snapshot["drafts"]],
    )


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
