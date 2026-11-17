"""Web 只负责授权与状态，不导入容器执行器，不访问 Docker socket。"""

from datetime import datetime, timedelta, timezone
import json

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.models.plugin import SandboxJob, SandboxWorkerState
from app.models.project_file import ProjectFile
from app.models.user import User
from app.models.tool_config import WorkspaceAgentPolicy
from app.schemas.plugins import SandboxPreviewCreate, SandboxCodePreviewCreate
from app.services.plugin_service import PluginError, PluginService
from app.services.sandbox_policy import LIMITS, SCRIPTS, normalize_domain, validate_requirement, script_digest, validate_code


def sandbox_availability(db: Session):
    recent = datetime.now(timezone.utc) - timedelta(seconds=30)
    state = db.scalar(select(SandboxWorkerState).where(
        SandboxWorkerState.heartbeat_at >= recent, SandboxWorkerState.status == "ready").limit(1))
    backend='bubblewrap' if state and state.detail.startswith('bubblewrap/') else 'docker'
    return {"available": bool(state), "backend": backend,
            "python_available": bool(state and 'python-stdlib' in state.detail), "reason": None if state else
            "无就绪隔离Worker。可配置Bubblewrap离线后端或符合要求的Rootless Docker；不回退宿主执行。"}


class SandboxService:
    def __init__(self, db: Session):
        self.db = db
        self.plugins = PluginService(db)

    def preview_code(self, user_id: str, payload: SandboxCodePreviewCreate):
        self.db.scalar(select(User).where(User.id == user_id).with_for_update())
        self.plugins.check_project(user_id, payload.project_id)
        policy = self.db.get(WorkspaceAgentPolicy, payload.project_id)
        if policy and policy.permission_mode == 'read_only':
            raise PluginError('当前项目只读，不能授权通用代码执行。')
        count = self.db.scalar(select(func.count()).select_from(SandboxJob).where(
            SandboxJob.user_id == user_id, SandboxJob.created_at >= datetime.now(timezone.utc)-timedelta(hours=1)))
        if count >= 20:
            raise PluginError('每小时最多创建20个执行预览。')
        code = validate_code(payload.code)
        files = self._authorized_files(user_id, payload.project_id, payload.file_ids)
        # 固定本次代码及授权副本；确认接口不接收可替换的代码或参数。
        plan = {'script': 'python_stdlib', 'code': code, 'files': files, 'domains': [],
                'dependencies': [], 'wheel_lock': [], 'backend': 'bubblewrap', 'limits': LIMITS}
        row = SandboxJob(user_id=user_id, project_id=payload.project_id,
            plugin_key='builtin.python', release_id='builtin', script_key='python_stdlib',
            plan_json=json.dumps(plan, ensure_ascii=False), status='awaiting_approval')
        self.db.add(row)
        self.db.commit()
        return self.public(row)

    def _authorized_files(self, user_id, project_id, file_ids):
        files = []
        total = 0
        for file_id in dict.fromkeys(file_ids):
            item = self.db.get(ProjectFile, file_id)
            if not item or item.user_id != user_id or item.project_id != project_id:
                raise PluginError('授权文件不属于当前工作区。')
            text = item.parsed_text or ''
            size = len(text.encode('utf-8'))
            if not size:
                raise PluginError('只能提供已有正文的资料副本。')
            total += size
            files.append({'id': item.id, 'name': item.file_name, 'bytes': size, 'text': text})
        if total > LIMITS['input_bytes']:
            raise PluginError('输入超过8MiB。')
        return files

    def authorize(self, row):
        """领取、确认和写回使用同一权限判定；代码执行不伪装成插件安装。"""
        if row.script_key == 'python_stdlib':
            self.plugins.check_project(row.user_id, row.project_id)
            policy = self.db.get(WorkspaceAgentPolicy, row.project_id)
            if policy and policy.permission_mode == 'read_only':
                raise PluginError('项目已变为只读，执行授权失效。')
            plan = json.loads(row.plan_json)
            if row.plugin_key != 'builtin.python' or plan.get('script') != 'python_stdlib':
                raise PluginError('通用任务类型不匹配。')
            validate_code(plan.get('code', ''))
            return
        installation, manifest = self.plugins.require_enabled(row.user_id, row.plugin_key, row.project_id)
        if installation.release_id != row.release_id or row.script_key not in manifest['scripts']:
            raise PluginError('插件版本或权限已变化，请重新预览。')

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
        files = self._authorized_files(user_id, payload.project_id, payload.file_ids)
        # 数据副本在授权预览时固定，Worker 不能读取后来新增的其他工作区内容。
        plan = {"files": files, "domains": domains, "dependencies": dependencies,
                "limits": LIMITS, "script": payload.script_key, "script_digest": script_digest(payload.script_key), "wheel_lock": []}
        plan['backend']=sandbox_availability(self.db).get('backend','docker')
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
        self.authorize(row)
        if row.status not in {"awaiting_approval", "awaiting_dependency_approval"}:
            return self.public(row)
        created = row.created_at.replace(tzinfo=row.created_at.tzinfo or timezone.utc)
        if datetime.now(timezone.utc) - created > timedelta(minutes=10):
            raise PluginError("授权预览已过期。")
        runtime = sandbox_availability(self.db)
        if not runtime["available"]:
            raise PluginError(runtime["reason"])
        if row.script_key == 'python_stdlib' and not runtime.get('python_available'):
            raise PluginError('通用Python3.12运行时未就绪，不能执行。')
        plan = json.loads(row.plan_json)
        if plan.get('backend','docker')!=runtime.get('backend','docker'):
            raise PluginError('隔离执行后端已变化，请重新预览确认。')
        if runtime.get('backend')=='bubblewrap' and (plan['domains'] or plan['dependencies']):
            raise PluginError('当前Bubblewrap仅支持离线、无动态依赖任务，请重新预览。')
        if row.script_key != 'python_stdlib' and plan.get("script_digest") != script_digest(row.script_key):
            raise PluginError("预览后审核脚本已变化，请重新预览。")
        row.status = "resolving_queued" if plan["dependencies"] and not plan.get("wheel_lock") else "queued"
        self.db.commit()
        return self.public(row)

    def cancel(self, user_id, job_id):
        row=self.db.scalar(select(SandboxJob).where(SandboxJob.id==job_id,SandboxJob.user_id==user_id).with_for_update())
        if not row:
            raise PluginError('任务不存在。')
        self.plugins.check_project(user_id,row.project_id)
        if row.status not in {'succeeded','failed','cancelled'}:
            row.status='cancelled'
            row.lease_version+=1
            row.error='用户取消；不导入迟到产物。'
            self.db.commit()
        return self.public(row)

    def public(self, row: SandboxJob):
        plan = json.loads(row.plan_json)
        runtime=sandbox_availability(self.db)
        if row.script_key == 'python_stdlib' and not runtime.get('python_available'):
            runtime = {**runtime, 'available': False, 'reason': '通用Python3.12运行时未就绪，不能执行。'}
        limits=({**LIMITS,'pids':1,'disk_bytes':0,'output_bytes':1024*1024,
                 'memory_limit_kind':'address_space','cpu_seconds':45} if plan.get('backend')=='bubblewrap' else LIMITS)
        return {"id": row.id, "project_id": row.project_id, "plugin_key": row.plugin_key,
                "script_key": row.script_key, "status": row.status, "error": row.error,
                'code': plan.get('code'),
                "files": [{k: f[k] for k in ("id", "name", "bytes")} for f in plan["files"]],
                "domains": plan["domains"], "dependencies": plan["dependencies"],
                "install_domains": ["pypi.org", "files.pythonhosted.org"] if plan["dependencies"] else [],
                "wheel_lock": plan.get("wheel_lock", []), "limits": limits,
                "runtime": runtime,
                "result": json.loads(row.result_json) if row.result_json else None,
                "notice": "仅执行上述授权副本；结果先隔离检查，不覆盖工作区。隔离不代表任意恶意代码安全保证。"}
