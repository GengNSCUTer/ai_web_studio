"""审核插件中心与沙箱授权接口；不提供用户自助发布或执行审核按钮。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.plugin import PluginSubmission, SandboxJob
from app.models.user import User
from app.schemas.plugins import PluginInstallConfirm, ProjectPluginUpdate, SandboxConfirm, SandboxPreviewCreate, SubmissionCreate
from app.services.plugin_service import PluginError, PluginService
from app.services.skill_catalog import SkillCatalogError
from app.services.sandbox_policy import SandboxPolicyError
from app.services.sandbox_service import SandboxService, sandbox_availability
from app.schemas.document_delivery import DocumentDraftCreate
from app.services.document_delivery_service import DocumentDeliveryService, DocumentDeliveryError

router = APIRouter(prefix="/plugins", tags=["plugins"])


def guarded(db, action):
    try:
        return action()
    except (PluginError, SandboxPolicyError, DocumentDeliveryError, SkillCatalogError) as exc:
        db.rollback()
        raise HTTPException(400, str(exc)) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, "状态被另一请求修改，请刷新后重试。") from exc


@router.get("")
def list_plugins(project_id: str | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return guarded(db, lambda: {"plugins": PluginService(db).list(user.id, project_id), "sandbox": sandbox_availability(db)})


@router.post("/submissions", status_code=201)
def submit_plugin(payload: SubmissionCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    # 来源只登记，不抓取、不安装、不信任客户端声明的审核/签名。
    item = PluginSubmission(user_id=user.id, **payload.model_dump(), status="pending_review")
    db.add(item)
    db.commit()
    return {"id": item.id, "status": item.status, "notice": "来源已登记，需管理员在仓库完成审核后发布；当前不可安装执行。"}


@router.get("/submissions")
def submissions(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return [{"id": x.id, "name": x.name, "kind": x.kind, "source_url": x.source_url, "status": x.status}
            for x in db.scalars(select(PluginSubmission).where(PluginSubmission.user_id == user.id).limit(100))]


@router.get("/sandbox/status")
def sandbox_status(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return sandbox_availability(db)


@router.post("/sandbox/preview", status_code=201)
def sandbox_preview(payload: SandboxPreviewCreate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return guarded(db, lambda: SandboxService(db).preview(user.id, payload))


@router.post("/sandbox/{job_id}/confirm")
def sandbox_confirm(job_id: str, payload: SandboxConfirm, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return guarded(db, lambda: SandboxService(db).confirm(user.id, job_id))


@router.get("/sandbox/jobs")
def sandbox_jobs(project_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    service = SandboxService(db)
    return guarded(db, lambda: _jobs(service, user.id, project_id))


def _jobs(service, user_id, project_id):
    service.plugins.check_project(user_id, project_id)
    return [service.public(row) for row in service.db.scalars(select(SandboxJob).where(
        SandboxJob.user_id == user_id, SandboxJob.project_id == project_id).order_by(SandboxJob.created_at.desc()).limit(30))]


@router.get("/sandbox/jobs/{job_id}")
def sandbox_job(job_id: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    service = SandboxService(db)
    return guarded(db, lambda: service.public(service.get(user.id, job_id)))


@router.post("/sandbox/jobs/{job_id}/artifacts/{artifact_index}/save", status_code=201)
def save_artifact(job_id: str, artifact_index: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    def save():
        service = SandboxService(db)
        row = service.get(user.id, job_id)
        if row.status != "succeeded" or not row.result_json:
            raise PluginError("仅可导入已完成且经过检查的产物。")
        import json
        artifacts = json.loads(row.result_json).get("artifacts", [])
        if artifact_index < 0 or artifact_index >= len(artifacts):
            raise PluginError("产物不存在。")
        artifact = artifacts[artifact_index]
        if len(artifact["content"]) > 500_000 or not artifact["content"].strip():
            raise PluginError("产物为空或超出单份草稿 500,000 字符限制，请分段整理。")
        return DocumentDeliveryService(db).create_draft(user_id=user.id, project_id=row.project_id,
            payload=DocumentDraftCreate(file_name=artifact["name"], content=artifact["content"], format="markdown",
                source_references=[f"sandbox-job:{row.id}", *[f"project-file:{f['id']}" for f in json.loads(row.plan_json)["files"]]]))
    return guarded(db, save)


@router.get("/{plugin_key}/preflight")
def preflight(plugin_key: str, project_id: str | None = None, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return guarded(db, lambda: PluginService(db).preflight(user.id, plugin_key, project_id))


@router.post("/{plugin_key}/install")
def install(plugin_key: str, payload: PluginInstallConfirm, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return guarded(db, lambda: _confirmed_install(PluginService(db), user.id, plugin_key, payload.expected_revision))


@router.post("/{plugin_key}/upgrade")
def upgrade(plugin_key: str, payload: PluginInstallConfirm, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    service = PluginService(db)
    def change():
        service.installed_release(user.id, plugin_key)
        return _confirmed_install(service, user.id, plugin_key, payload.expected_revision)
    return guarded(db, change)


@router.post("/{plugin_key}/rollback")
def rollback(plugin_key: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return guarded(db, lambda: _change(PluginService(db), "rollback", user.id, plugin_key))


@router.delete("/{plugin_key}")
def uninstall(plugin_key: str, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    return guarded(db, lambda: _change(PluginService(db), "uninstall", user.id, plugin_key))


@router.put("/{plugin_key}/project")
def enable(plugin_key: str, payload: ProjectPluginUpdate, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    service = PluginService(db)
    def update():
        installation, _ = service.installed_release(user.id, plugin_key)
        if installation.release_id != payload.expected_release_id:
            raise PluginError("插件安装版本已变化，请刷新并重新查看权限。")
        return _change(service, "enable", user.id, plugin_key, payload.project_id, payload.enabled)
    return guarded(db, update)


def _confirmed_install(service, user_id, plugin_key, revision):
    if service.preflight(user_id, plugin_key)["catalog_revision"] != revision:
        raise PluginError("预览后插件内容或权限已变化，请重新预检。")
    return _change(service, "install", user_id, plugin_key)


def _change(service, action, *args):
    getattr(service, action)(*args)
    return {"ok": True}
