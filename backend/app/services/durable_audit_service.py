from __future__ import annotations

"""Durable Run 的默认脱敏审计导出。"""

import hashlib
import json
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent_runtime import AgentArtifact, AgentCheckpoint, AgentOutboxEvent, AgentRun, AgentStep


class DurableAuditError(ValueError):
    """审计对象不存在或不属于当前用户。"""


class DurableAuditService:
    """把 Durable 状态转换成不含原始输入/正文的 JSONL 事件。"""

    @staticmethod
    def _time(value: datetime | None) -> str | None:
        return value.isoformat() if value else None

    @staticmethod
    def _hash(value: Any) -> str:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()

    @staticmethod
    def _event(event_type: str, occurred_at: datetime | None, **payload: Any) -> dict[str, Any]:
        return {
            "event_type": event_type,
            "occurred_at": DurableAuditService._time(occurred_at),
            **payload,
        }

    def _load_run(self, *, run_id: str, user_id: str) -> AgentRun:
        run = self.db.scalars(
            select(AgentRun)
            .where(
                AgentRun.id == run_id,
                AgentRun.user_id == user_id,
                AgentRun.runtime_kind == "durable_tool_workflow",
            )
            .limit(1)
        ).first()
        if not run:
            raise DurableAuditError("Durable Run 不存在或当前用户无权查看。")
        return run

    def __init__(self, db: Session) -> None:
        self.db = db

    def build_events(self, *, run_id: str, user_id: str) -> list[dict[str, Any]]:
        run = self._load_run(run_id=run_id, user_id=user_id)
        try:
            planner_state = json.loads(run.planner_state_json or "{}")
        except json.JSONDecodeError:
            planner_state = {}
        planner_state = planner_state if isinstance(planner_state, dict) else {}
        snapshots = planner_state.get("tool_snapshots")
        has_snapshot = isinstance(snapshots, dict) and bool(snapshots)
        skill = planner_state.get("skill") if isinstance(planner_state.get("skill"), dict) else None
        steps = list(
            self.db.scalars(select(AgentStep).where(AgentStep.run_id == run.id).order_by(AgentStep.sequence)).all()
        )
        checkpoints = list(
            self.db.scalars(
                select(AgentCheckpoint)
                .where(AgentCheckpoint.run_id == run.id)
                .order_by(AgentCheckpoint.state_version.asc())
            ).all()
        )
        artifacts = list(
            self.db.scalars(
                select(AgentArtifact).where(AgentArtifact.run_id == run.id).order_by(AgentArtifact.created_at.asc())
            ).all()
        )
        outbox = list(
            self.db.scalars(
                select(AgentOutboxEvent)
                .where(AgentOutboxEvent.run_id == run.id)
                .order_by(AgentOutboxEvent.created_at.asc())
            ).all()
        )
        events: list[dict[str, Any]] = [
            self._event(
                "run.summary",
                run.created_at,
                run_id=run.id,
                project_id=run.project_id,
                conversation_id=run.conversation_id,
                status=run.status,
                state_version=run.state_version,
                current_step=run.current_step,
                max_steps=run.max_steps,
                finished_at=self._time(run.finished_at),
                legacy_snapshot=not has_snapshot,
            ),
            self._event(
                "run.execution_snapshot",
                run.created_at,
                run_id=run.id,
                skill=(
                    {
                        "skill_key": skill.get("skill_key"),
                        "version": skill.get("version"),
                        "manifest_digest": skill.get("manifest_digest"),
                        "durable_eligible": bool(skill.get("durable_eligible")),
                    }
                    if skill
                    else None
                ),
                tools=(
                    {
                        str(key): {
                            "tool_key": value.get("tool_key"),
                            "provider": value.get("provider"),
                            "category": value.get("category"),
                            "source_type": value.get("source_type"),
                            "risk_level": value.get("risk_level"),
                            "read_only": bool(value.get("read_only")),
                            "fingerprint": value.get("fingerprint"),
                        }
                        for key, value in snapshots.items()
                        if isinstance(value, dict)
                    }
                    if has_snapshot
                    else {}
                ),
            ),
        ]
        for step in steps:
            try:
                dependencies = json.loads(step.depends_on_json or "[]")
            except json.JSONDecodeError:
                dependencies = []
            events.append(
                self._event(
                    "step.summary",
                    step.finished_at or step.started_at or step.created_at,
                    run_id=run.id,
                    step_id=step.id,
                    sequence=step.sequence,
                    call_id=step.call_id,
                    tool_key=step.tool_key,
                    status=step.status,
                    attempts=step.attempts,
                    max_attempts=step.max_attempts,
                    arguments_hash=step.arguments_hash,
                    depends_on=dependencies if isinstance(dependencies, list) else [],
                    lease_version=step.lease_version,
                    error_code=step.error_code,
                    error_message_hash=self._hash(step.error_message) if step.error_message else None,
                    started_at=self._time(step.started_at),
                    finished_at=self._time(step.finished_at),
                )
            )
        for checkpoint in checkpoints:
            try:
                observations = json.loads(checkpoint.observations_json or "[]")
            except json.JSONDecodeError:
                observations = []
            observation_types = [
                str(item.get("type"))
                for item in observations
                if isinstance(item, dict) and item.get("type")
            ] if isinstance(observations, list) else []
            events.append(
                self._event(
                    "checkpoint.summary",
                    checkpoint.created_at,
                    run_id=run.id,
                    checkpoint_id=checkpoint.id,
                    step_sequence=checkpoint.step_sequence,
                    state_version=checkpoint.state_version,
                    observation_types=observation_types,
                    observation_count=len(observation_types),
                    remaining_budget=self._safe_budget(checkpoint.remaining_budget_json),
                )
            )
        for artifact in artifacts:
            events.append(
                self._event(
                    "artifact.summary",
                    artifact.created_at,
                    run_id=run.id,
                    artifact_id=artifact.id,
                    step_id=artifact.step_id,
                    artifact_type=artifact.artifact_type,
                    content_hash=artifact.content_hash,
                    char_count=artifact.char_count,
                    prompt_state=artifact.prompt_state,
                )
            )
        for event in outbox:
            events.append(
                self._event(
                    "outbox.summary",
                    event.created_at,
                    run_id=run.id,
                    event_id=event.id,
                    step_id=event.step_id,
                    event_type_value=event.event_type,
                    status=event.status,
                    attempt_count=event.attempt_count,
                    lease_version=event.lease_version,
                    error_code=event.error_code,
                    error_message_hash=self._hash(event.error_message) if event.error_message else None,
                    updated_at=self._time(event.updated_at),
                )
            )
        return events

    @staticmethod
    def _safe_budget(raw: str | None) -> dict[str, Any]:
        try:
            value = json.loads(raw or "{}")
        except json.JSONDecodeError:
            return {}
        if not isinstance(value, dict):
            return {}
        return {
            str(key): value[key]
            for key in ("remaining_steps", "remaining_rounds", "remaining_calls")
            if key in value and isinstance(value[key], (int, float))
        }

    def export_jsonl(self, *, run_id: str, user_id: str) -> str:
        events = self.build_events(run_id=run_id, user_id=user_id)
        return "".join(
            json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
            for event in events
        )
