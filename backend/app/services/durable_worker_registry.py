from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.agent_runtime import AgentOutboxEvent, AgentRun, AgentStep, AgentWorkerHeartbeat


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


class DurableWorkerRegistry:
    STALE_SECONDS = 90
    QUEUED_ALERT_SECONDS = 600

    def __init__(self, db: Session) -> None:
        self.db = db

    def check_database(self) -> None:
        for model in (AgentRun, AgentStep, AgentOutboxEvent, AgentWorkerHeartbeat):
            self.db.execute(select(model.__table__.c[0]).limit(1))
        self.db.execute(text("select 1"))

    def register(self, *, owner: str, instance_id: str | None = None) -> str:
        instance_id = instance_id or str(uuid4())
        now = _now()
        try:
            row = self.db.get(AgentWorkerHeartbeat, owner, with_for_update=True)
            if row and row.status != "stopped" and _aware(row.heartbeat_at) >= now - timedelta(seconds=self.STALE_SECONDS):
                raise RuntimeError("同名 Durable Worker 仍在线，请为每个进程设置唯一 AGENT_WORKER_ID。")
            if row:
                row.instance_id = instance_id
                row.status = "idle"
                row.current_step_id = None
                row.started_at = now
                row.heartbeat_at = now
                row.stopped_at = None
            else:
                self.db.add(AgentWorkerHeartbeat(
                    owner=owner, instance_id=instance_id, status="idle",
                    started_at=now, heartbeat_at=now,
                ))
            self.db.commit()
        except IntegrityError as exc:
            self.db.rollback()
            raise RuntimeError("同名 Durable Worker 已被其他进程注册。") from exc
        return instance_id

    def heartbeat(self, *, owner: str, instance_id: str, status: str, step_id: str | None = None) -> bool:
        row = self.db.get(AgentWorkerHeartbeat, owner, with_for_update=True)
        if not row or row.instance_id != instance_id or row.status == "stopped":
            self.db.rollback()
            return False
        row.status = status
        row.current_step_id = step_id
        row.heartbeat_at = _now()
        self.db.commit()
        return True

    def stop(self, *, owner: str, instance_id: str) -> None:
        row = self.db.get(AgentWorkerHeartbeat, owner, with_for_update=True)
        if row and row.instance_id == instance_id:
            row.status = "stopped"
            row.current_step_id = None
            row.stopped_at = _now()
            row.heartbeat_at = row.stopped_at
            self.db.commit()
        else:
            self.db.rollback()

    def summary(self, *, user_id: str) -> dict[str, object]:
        now = _now()
        cutoff = now - timedelta(seconds=self.STALE_SECONDS)
        workers = list(self.db.scalars(select(AgentWorkerHeartbeat)).all())
        online = [row for row in workers if row.status != "stopped" and _aware(row.heartbeat_at) >= cutoff]
        stale = [row for row in workers if row.status != "stopped" and _aware(row.heartbeat_at) < cutoff]
        queued_cutoff = now - timedelta(seconds=self.QUEUED_ALERT_SECONDS)
        queued_overdue = int(self.db.scalar(
            select(func.count()).select_from(AgentOutboxEvent)
            .join(AgentRun, AgentRun.id == AgentOutboxEvent.run_id)
            .where(
                AgentRun.user_id == user_id,
                AgentRun.runtime_kind == "durable_tool_workflow",
                AgentOutboxEvent.status == "pending",
                AgentOutboxEvent.available_at <= now,
                AgentOutboxEvent.created_at < queued_cutoff,
            )
        ) or 0)
        pending = int(self.db.scalar(
            select(func.count()).select_from(AgentOutboxEvent)
            .join(AgentRun, AgentRun.id == AgentOutboxEvent.run_id)
            .where(
                AgentRun.user_id == user_id,
                AgentRun.runtime_kind == "durable_tool_workflow",
                AgentOutboxEvent.status == "pending",
                AgentOutboxEvent.available_at <= now,
            )
        ) or 0)
        alerts: list[dict[str, object]] = []
        if pending and not online:
            alerts.append({"code": "durable_no_online_worker", "severity": "critical", "count": pending})
        if stale:
            alerts.append({"code": "durable_worker_stale", "severity": "warning", "count": len(stale)})
        if queued_overdue:
            alerts.append({"code": "durable_queue_overdue", "severity": "warning", "count": queued_overdue})
        return {
            "online_workers": len(online),
            "busy_workers": sum(row.status == "running" for row in online),
            "stale_workers": len(stale),
            "pending_events": pending,
            "overdue_events": queued_overdue,
            "last_heartbeat_at": max((row.heartbeat_at for row in workers), default=None),
            "alerts": alerts,
        }
