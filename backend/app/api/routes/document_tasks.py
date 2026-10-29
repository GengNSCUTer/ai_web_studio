"""文档任务/产物资源；审批继续使用 agent-runtime 的挑战与确认接口。"""

import base64
import json
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.agent_runtime import AgentArtifact, AgentRun
from app.models.user import User
from app.schemas.document_task import DocumentTaskCreate
from app.services.agent_runtime_service import AgentRuntimeError
from app.services.document_task_service import DocumentTaskService, DocumentTaskError, KIND

router = APIRouter(prefix="/document-tasks", tags=["document-tasks"])


def guarded(db, action):
    try:
        return action()
    except (DocumentTaskError, AgentRuntimeError) as exc:
        db.rollback()
        raise HTTPException(409, str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "任务状态并发变化，请使用相同幂等键刷新重试。") from exc


@router.post("", status_code=202)
def create_task(payload: DocumentTaskCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return guarded(db, lambda: DocumentTaskService(db).create(user.id, payload))


@router.get("")
def list_tasks(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    service = DocumentTaskService(db)
    ids = list(db.scalars(select(AgentRun.id).where(AgentRun.user_id == user.id, AgentRun.runtime_kind == KIND)
                          .order_by(AgentRun.created_at.desc()).limit(50)))
    return guarded(db, lambda: [service.snapshot(user.id, run_id) for run_id in ids])


@router.get("/{run_id}")
def get_task(run_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return guarded(db, lambda: DocumentTaskService(db).snapshot(user.id, run_id))


@router.post("/{run_id}/cancel")
def cancel_task(run_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return guarded(db, lambda: DocumentTaskService(db).cancel(user.id, run_id))


@router.post("/{run_id}/resume")
def resume_task(run_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return guarded(db, lambda: DocumentTaskService(db).resume(user.id, run_id))


def artifact_for_user(db, user_id, run_id, artifact_id):
    DocumentTaskService(db).get(user_id, run_id)
    item = db.scalar(select(AgentArtifact).where(AgentArtifact.id == artifact_id,
                     AgentArtifact.run_id == run_id, AgentArtifact.user_id == user_id))
    if not item:
        raise DocumentTaskError("产物不存在或无权访问。")
    return item, json.loads(item.content_json)


@router.get("/{run_id}/artifacts/{artifact_id}")
def read_artifact(run_id: str, artifact_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    def read():
        item, payload = artifact_for_user(db, user.id, run_id, artifact_id)
        return {"id": item.id, "type": item.artifact_type, "content": {k: v for k, v in payload.items() if k != "data_base64"}}
    return guarded(db, read)


@router.get("/{run_id}/artifacts/{artifact_id}/download")
def download_artifact(run_id: str, artifact_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    def download():
        item, payload = artifact_for_user(db, user.id, run_id, artifact_id)
        if item.artifact_type != "document_export":
            raise DocumentTaskError("该阶段不是可下载文档。")
        data = base64.b64decode(payload["data_base64"], validate=True)
        return Response(data, media_type=payload["mime_type"], headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(payload['file_name'])}",
            "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
    return guarded(db, download)
