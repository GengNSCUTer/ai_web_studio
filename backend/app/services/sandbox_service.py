"""Web 只负责授权与状态，不导入容器执行器，不访问 Docker socket。"""

from datetime import datetime, timedelta, timezone
import json

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.models.plugin import SandboxJob, SandboxWorkerState
from app.models.project_file import ProjectFile
from app.models.user import User
from app.schemas.plugins import SandboxPreviewCreate
from app.services.plugin_service import PluginError, PluginService
from app.services.sandbox_policy import LIMITS, SCRIPTS, normalize_domain, validate_requirement, script_digest


def sandbox_availability(db: Session):
    recent = datetime.now(timezone.utc) - timedelta(seconds=30)
    state = db.scalar(select(SandboxWorkerState).where(
        SandboxWorkerState.heartbeat_at >= recent, SandboxWorkerState.status == "ready").limit(1))
    return {"available": bool(state), "reason": None if state else
            "无可用独立沙箱 Worker。需独立账号的 Rootless Docker、cgroup v2 限额和隔离出口；不回退宿主执行。"}


class SandboxService:
    def __init__(self, db: Session):
        self.db = db
        self.plugins = PluginService(db)

    def preview(self, user_id: str, payload: SandboxPreviewCreate):
        self.db.scalar(select(User).where(User.id == user_id).with_for_update())
        pending = self.db.scalar(select(func.count()).select_from(SandboxJob).where(
            SandboxJob.user_id == user_id, SandboxJob.created_at >= datetime.now(timezone.utc) - timedelta(hours=1)))
        if pending >= 20:
            raise PluginError("每小时最多创建 20 个脚本授权预览，请稍后再试。")
        installation, manifest = self.plugins.require_enabled(user_id, payload.plugin_key, payload.project_id)
        script = SCRIPTS.get(payload.script_key)
        if not script or payload.script_key not in manifest.get("scripts", []):
            raise PluginError("脚本未在当前已审核插件版本中发布。")
        domains = sorted({normalize_domain(value) for value in payload.domains})
        dependencies = sorted({validate_requirement(value) for value in payload.dependencies})
        if not set(domains).issubset(script["domains"]) or not set(dependencies).issubset(script["dependencies"]):
            raise PluginError("新增联网域名或依赖必须先审核插件版本，不能静默扩权。")
        files = []
        total = 0
        for file_id in dict.fromkeys(payload.file_ids):
            item = self.db.get(ProjectFile, file_id)
            if not item or item.user_id != user_id or item.project_id != payload.project_id:
                raise PluginError("授权文件不属于当前工作区。")
            size = len((item.parsed_text or "").encode("utf-8"))
            if not size:
                raise PluginError("当前脚本仅支持已提取正文的文档副本。")
            total += size
            files.append({"id": item.id, "name": item.file_name, "bytes": size, "text": item.parsed_text})
        if total > LIMITS["input_bytes"]:
            raise PluginError("授权文件副本总量超过沙箱输入限额。")
        # 数据副本在授权预览时固定，Worker 不能读取后来新增的其他工作区内容。
        plan = {"files": files, "domains": domains, "dependencies": dependencies,
                "limits": LIMITS, "script": payload.script_key, "script_digest": script_digest(payload.script_key), "wheel_lock": []}
        row = SandboxJob(user_id=user_id, project_id=payload.project_id, plugin_key=payload.plugin_key,
                         release_id=installation.release_id, script_key=payload.script_key,
                         status="awaiting_approval", plan_json=json.dumps(plan, ensure_ascii=False))
        self.db.add(row)
        self.db.commit()
        return self.public(row)

    def get(self, user_id: str, job_id: str):
        row = self.db.get(SandboxJob, job_id)
        if not row or row.user_id != user_id:
            raise PluginError("任务不存在。")
        self.plugins.check_project(user_id, row.project_id)
        return row

    def confirm(self, user_id: str, job_id: str):
        row = self.db.scalar(select(SandboxJob).where(
            SandboxJob.id == job_id, SandboxJob.user_id == user_id).with_for_update())
        if not row:
            raise PluginError("任务不存在。")
        installation, manifest = self.plugins.require_enabled(user_id, row.plugin_key, row.project_id)
        if installation.release_id != row.release_id or row.script_key not in manifest["scripts"]:
            raise PluginError("插件版本或权限已变化，请重新预览。")
        if row.status not in {"awaiting_approval", "awaiting_dependency_approval"}:
            return self.public(row)
        created = row.created_at.replace(tzinfo=row.created_at.tzinfo or timezone.utc)
        if datetime.now(timezone.utc) - created > timedelta(minutes=10):
            raise PluginError("授权预览已过期。")
        if not sandbox_availability(self.db)["available"]:
            raise PluginError(sandbox_availability(self.db)["reason"])
        plan = json.loads(row.plan_json)
        if plan.get("script_digest") != script_digest(row.script_key):
            raise PluginError("预览后审核脚本已变化，请重新预览。")
        row.status = "resolving_queued" if plan["dependencies"] and not plan.get("wheel_lock") else "queued"
        self.db.commit()
        return self.public(row)

    def public(self, row: SandboxJob):
        plan = json.loads(row.plan_json)
        return {"id": row.id, "project_id": row.project_id, "plugin_key": row.plugin_key,
                "script_key": row.script_key, "status": row.status, "error": row.error,
                "files": [{k: f[k] for k in ("id", "name", "bytes")} for f in plan["files"]],
                "domains": plan["domains"], "dependencies": plan["dependencies"],
                "install_domains": ["pypi.org", "files.pythonhosted.org"] if plan["dependencies"] else [],
                "wheel_lock": plan.get("wheel_lock", []), "limits": LIMITS,
                "runtime": sandbox_availability(self.db),
                "result": json.loads(row.result_json) if row.result_json else None,
                "notice": "仅执行上述授权副本；结果先隔离检查，不覆盖工作区。容器不是任意恶意代码安全保证。"}
