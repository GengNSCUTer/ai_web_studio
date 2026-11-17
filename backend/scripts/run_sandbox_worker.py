"""独立进程的沙箱Worker；Docker要求独立账号，原生后端仍须namespace/seccomp。"""

from datetime import datetime, timedelta, timezone
import json
import os
import signal
import threading
import time
from uuid import uuid4

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.plugin import SandboxJob, SandboxWorkerState
from app.services.sandbox_service import SandboxService
from app.services.sandbox_policy import SandboxPolicyError
from sandbox.runner import DockerSandbox, SandboxUnavailable


def main():
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    owner = f"sandbox-{os.getpid()}-{uuid4().hex[:12]}"
    backend = os.environ.get('AIWS_SANDBOX_BACKEND','docker')
    if backend == 'bubblewrap':
        from sandbox.bwrap_runner import BubblewrapSandbox
        runtime = BubblewrapSandbox()
    elif backend == 'docker':
        runtime = DockerSandbox()
    else:
        raise ValueError('未知沙箱后端，禁止降级执行。')
    try:
        detail = runtime.preflight()
    except Exception as exc:
        # 只报告固定诊断，不输出环境变量或 Docker 原始错误。
        print(f"沙箱不可用：{exc}", flush=True)
        return 2

    def heartbeat():
        while not stop.is_set():
            with SessionLocal() as db:
                db.merge(SandboxWorkerState(owner=owner, status="ready", detail=detail,
                                             heartbeat_at=datetime.now(timezone.utc)))
                db.commit()
            stop.wait(5)

    thread = threading.Thread(target=heartbeat, daemon=True)
    thread.start()
    try:
        while not stop.is_set():
            now = datetime.now(timezone.utc)
            with SessionLocal() as db:
                # 旧任务可能有外部请求副作用，不自动重放；用户确认后另起新任务。
                for expired in db.scalars(select(SandboxJob).where(
                        SandboxJob.status.in_(["running", "resolving"]), SandboxJob.lease_expires_at < now)
                        .with_for_update(skip_locked=True)):
                    expired.status = "failed"
                    expired.error = "Worker 租约过期；产物未导入，请重新预览授权。"
                    expired.lease_version += 1
                job = db.scalar(select(SandboxJob).where(SandboxJob.status.in_(["queued", "resolving_queued"]))
                                .order_by(SandboxJob.created_at).with_for_update(skip_locked=True).limit(1))
                if not job:
                    db.commit()
                    stop.wait(1)
                    continue
                resolving = job.status == "resolving_queued"
                try:
                    SandboxService(db).authorize(job)
                    authorized = True
                except ValueError:
                    authorized = False
                if not authorized:
                    job.status = "failed"
                    job.error = "执行前插件版本或授权已变化。"
                    db.commit()
                    continue
                job.status = "resolving" if resolving else "running"
                job.lease_owner = owner
                job.lease_version += 1
                job.lease_expires_at = now + timedelta(seconds=180)
                version, job_id = job.lease_version, job.id
                plan = json.loads(job.plan_json)
                if plan.get('backend','docker')!=backend:
                    job.status,job.error='failed','隔离后端已变化，请重新预览授权。'
                    db.commit()
                    continue
                db.commit()
            result, error = None, None
            try:
                last_check = 0.0
                lost = False
                def cancelled():
                    nonlocal last_check, lost
                    if stop.is_set():
                        return True
                    if time.monotonic() - last_check < .25:
                        return lost
                    last_check = time.monotonic()
                    with SessionLocal() as db:
                        current=db.get(SandboxJob,job_id)
                        lost = (not current or current.status!='running' or current.lease_owner!=owner
                                or current.lease_version!=version)
                        return lost
                result = (runtime.resolve_dependencies(plan) if resolving else
                          runtime.execute(plan,cancelled=cancelled) if backend=='bubblewrap' else runtime.execute(plan))
            except (SandboxUnavailable, SandboxPolicyError) as exc:
                error = str(exc)[:256]
            except Exception:
                error = "隔离执行失败（环境、超时、资源、网络、依赖或产物校验）；未放宽限制，未覆盖工作区。"
            with SessionLocal() as db:
                job = db.scalar(select(SandboxJob).where(SandboxJob.id == job_id).with_for_update())
                if (not job or job.lease_owner != owner or job.lease_version != version
                        or job.status not in {"running", "resolving"}):
                    continue
                expiry = job.lease_expires_at.replace(tzinfo=job.lease_expires_at.tzinfo or timezone.utc)
                if expiry < datetime.now(timezone.utc):
                    job.status, job.error = "failed", "执行结果超过租约，禁止写回。"
                elif error:
                    job.status, job.error = "failed", error
                else:
                    try:
                        SandboxService(db).authorize(job)
                    except ValueError:
                        job.status, job.error = "failed", "执行期间插件已停用或版本变化；结果拒绝入库。"
                    else:
                        if resolving:
                            plan["wheel_lock"] = result
                            # 安装前向用户展示完整传递依赖；不能只批准直接依赖。
                            job.plan_json = json.dumps(plan, ensure_ascii=False)
                            job.created_at = datetime.now(timezone.utc)
                            job.status = "awaiting_dependency_approval"
                        else:
                            job.result_json = json.dumps(result, ensure_ascii=False)
                            job.status = "succeeded"
                db.commit()
    finally:
        stop.set()
        thread.join(timeout=6)
        with SessionLocal() as db:
            row = db.get(SandboxWorkerState, owner)
            if row:
                row.status = "stopped"
                db.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
