from __future__ import annotations

"""Durable execution for bounded, read-only Tool DAGs.

The existing chat path stays synchronous for short contextual lookups. This module
is the separate control plane for work that may outlive an HTTP request: a Run and
all of its Steps are committed with an outbox event, then a worker obtains a lease
before executing exactly one Step. It deliberately accepts only low-risk read-only
tools; file edits keep using the approval-specific continuation.
"""

import asyncio
import hashlib
import json
import logging
import socket
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.agent_runtime import AgentArtifact, AgentCheckpoint, AgentOutboxEvent, AgentRun, AgentStep
from app.models.conversation import Conversation
from app.models.knowledge import KnowledgeRetrievalLog
from app.models.message import Message
from app.models.observability import ChatRuntimeMetric
from app.models.project import Project
from app.models.tool_trace import ToolCallRun, ToolRouteRun
from app.services.tools.catalog import ToolCatalog
from app.services.tools.bindings import ToolResultBindingError, ToolResultBindingResolver
from app.services.tools.executor import ToolExecutor
from app.services.tools.schemas import PlannedToolCall, ToolResultBinding
from app.services.tools.validation import ToolSchemaValidationError, ToolSchemaValidator


logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DurableToolRuntimeError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class DurableStepClaim:
    outbox_event_id: str
    outbox_lease_version: int
    step_id: str


class DurableToolRunService:
    """Creates and observes database-backed read-only Tool workflows."""

    MAX_STEPS = 12
    DEFAULT_MAX_ATTEMPTS = 3
    LEASE_SECONDS = 90

    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def enqueue(
        self,
        *,
        user_id: str,
        project_id: str | None,
        conversation_id: str | None,
        assistant_message_id: str | None,
        calls: list[dict[str, Any]],
        idempotency_key: str | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> AgentRun:
        if not calls or len(calls) > self.MAX_STEPS:
            raise DurableToolRuntimeError("invalid_step_count", f"Tool Run 需包含 1 到 {self.MAX_STEPS} 个 Step。")
        max_attempts = max(1, min(int(max_attempts), 5))
        self._validate_scope(
            user_id=user_id,
            project_id=project_id,
            conversation_id=conversation_id,
            assistant_message_id=assistant_message_id,
        )
        catalog = ToolCatalog(db=self.db, user_id=user_id)
        validator = ToolSchemaValidator()
        normalized_calls: list[dict[str, Any]] = []
        call_ids: set[str] = set()

        for index, raw in enumerate(calls, start=1):
            if not isinstance(raw, dict):
                raise DurableToolRuntimeError("invalid_step", "每个 Tool Step 必须是对象。")
            tool_key = str(raw.get("tool_key") or "").strip()
            definition = catalog.get_or_none(tool_key)
            if not definition:
                raise DurableToolRuntimeError("unknown_tool", f"未找到工具：{tool_key or 'unknown'}")
            if not definition.read_only or definition.risk_level != "low":
                raise DurableToolRuntimeError(
                    "unsafe_tool_not_supported",
                    f"可恢复队列当前只接受低风险只读工具，{tool_key} 必须走专用审批链路。",
                )
            arguments = raw.get("arguments") or {}
            if not isinstance(arguments, dict):
                raise DurableToolRuntimeError("invalid_arguments", f"{tool_key} 的 arguments 必须是对象。")
            depends_on = raw.get("depends_on") or []
            bindings = raw.get("result_bindings") or []
            if not isinstance(depends_on, list) or not all(isinstance(item, str) and item for item in depends_on):
                raise DurableToolRuntimeError("invalid_dependencies", f"{tool_key} 的 depends_on 非法。")
            if not isinstance(bindings, list) or not all(isinstance(item, dict) for item in bindings):
                raise DurableToolRuntimeError("invalid_bindings", f"{tool_key} 的 result_bindings 非法。")
            normalized_bindings: list[dict[str, Any]] = []
            for raw_binding in bindings:
                try:
                    binding = ToolResultBinding(**raw_binding)
                    if not isinstance(binding.required, bool):
                        raise ToolResultBindingError("绑定 required 必须是布尔值。")
                    ToolResultBindingResolver.validate_declaration(binding)
                except (TypeError, ToolResultBindingError) as exc:
                    raise DurableToolRuntimeError("invalid_bindings", str(exc)) from exc
                normalized_bindings.append(
                    {
                        "source_call_id": binding.source_call_id,
                        "source_path": binding.source_path,
                        "target_argument": binding.target_argument,
                        "required": binding.required,
                    }
                )
            bindings = normalized_bindings
            binding_targets = {item["target_argument"] for item in bindings}
            try:
                arguments = validator.validate(
                    definition=definition,
                    arguments=arguments,
                    deferred_required_fields={item for item in binding_targets if item},
                )
            except ToolSchemaValidationError as exc:
                raise DurableToolRuntimeError("schema_invalid", str(exc)) from exc
            call_id = str(raw.get("call_id") or f"step-{index}").strip()
            if not call_id or len(call_id) > 64:
                raise DurableToolRuntimeError("invalid_call_id", "Tool Run 的 call_id 长度必须为 1 到 64。")
            if call_id in call_ids:
                raise DurableToolRuntimeError("duplicate_call_id", "同一个 Tool Run 内 call_id 必须唯一。")
            call_ids.add(call_id)
            normalized_calls.append(
                {
                    "call_id": call_id,
                    "tool_key": tool_key,
                    "arguments": arguments,
                    "depends_on": depends_on,
                    "result_bindings": bindings,
                }
            )

        for call in normalized_calls:
            if any(dependency not in call_ids or dependency == call["call_id"] for dependency in call["depends_on"]):
                raise DurableToolRuntimeError("invalid_dependencies", "依赖必须引用同一个 Run 内的其他 Step。")
            for binding in call["result_bindings"]:
                source = str(binding.get("source_call_id") or "")
                target = str(binding.get("target_argument") or "")
                if source not in call_ids or not target:
                    raise DurableToolRuntimeError("invalid_bindings", "结果绑定必须声明已有 source_call_id 和 target_argument。")
                if source not in call["depends_on"]:
                    raise DurableToolRuntimeError("invalid_bindings", "结果绑定的来源必须同时列在 depends_on 中。")
        # DAG 可以不按 sequence 声明，但不能让两个 Step 互相等待直到 lease 重试耗尽。
        unresolved = {item["call_id"]: set(item["depends_on"]) for item in normalized_calls}
        ready = [call_id for call_id, dependencies in unresolved.items() if not dependencies]
        resolved_count = 0
        while ready:
            current = ready.pop()
            resolved_count += 1
            for call_id, dependencies in unresolved.items():
                if current in dependencies:
                    dependencies.remove(current)
                    if not dependencies:
                        ready.append(call_id)
        if resolved_count != len(unresolved):
            raise DurableToolRuntimeError("cyclic_dependencies", "Tool DAG 不能包含循环依赖。")

        canonical = self._json(
            {
                "project_id": project_id,
                "conversation_id": conversation_id,
                "assistant_message_id": assistant_message_id,
                "calls": normalized_calls,
                "max_attempts": max_attempts,
            }
        )
        request_hash = self._hash(canonical)
        if idempotency_key is not None and not idempotency_key.strip():
            raise DurableToolRuntimeError("invalid_idempotency_key", "幂等键不能为空白字符串。")
        key_material = idempotency_key.strip() if idempotency_key is not None else request_hash
        scoped_key = f"durable-tool:{user_id}:{key_material}"[:192]
        existing = self.db.scalars(select(AgentRun).where(AgentRun.idempotency_key == scoped_key).limit(1)).first()
        if existing:
            if self._request_hash(existing) != request_hash:
                raise DurableToolRuntimeError(
                    "idempotency_conflict",
                    "同一个幂等键已绑定到不同的 Tool Run 请求。",
                )
            return existing

        run = AgentRun(
            user_id=user_id,
            project_id=project_id,
            conversation_id=conversation_id,
            assistant_message_id=assistant_message_id,
            runtime_kind="durable_tool_workflow",
            status="queued",
            input_json=self._json({"calls": normalized_calls}),
            planner_state_json=self._json(
                {
                    "mode": "read_only_durable",
                    "call_ids": [item["call_id"] for item in normalized_calls],
                    "request_hash": request_hash,
                }
            ),
            idempotency_key=scoped_key,
            max_steps=len(normalized_calls),
            current_step=0,
        )
        self.db.add(run)
        self.db.flush()
        for sequence, call in enumerate(normalized_calls, start=1):
            arguments_json = self._json(call["arguments"])
            step = AgentStep(
                run_id=run.id,
                sequence=sequence,
                call_id=call["call_id"],
                tool_key=call["tool_key"],
                arguments_json=arguments_json,
                arguments_hash=self._hash(arguments_json),
                status="pending",
                depends_on_json=self._json(call["depends_on"]),
                result_bindings_json=self._json(call["result_bindings"]),
                max_attempts=max_attempts,
                available_at=utcnow(),
            )
            self.db.add(step)
            self.db.flush()
            self.db.add(
                AgentOutboxEvent(
                    event_key=f"agent-step:{run.id}:{step.id}:requested",
                    run_id=run.id,
                    step_id=step.id,
                    payload_json=self._json({"run_id": run.id, "step_id": step.id, "sequence": sequence}),
                    available_at=utcnow(),
                )
            )
        run.state_version = 1
        self._checkpoint(run, step=None, observations=[{"type": "run_enqueued", "steps": len(normalized_calls)}])
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            existing = self.db.scalars(select(AgentRun).where(AgentRun.idempotency_key == scoped_key).limit(1)).first()
            if not existing:
                raise
            if self._request_hash(existing) != request_hash:
                raise DurableToolRuntimeError(
                    "idempotency_conflict",
                    "同一个幂等键已绑定到不同的 Tool Run 请求。",
                )
            return existing
        return run

    def _validate_scope(
        self,
        *,
        user_id: str,
        project_id: str | None,
        conversation_id: str | None,
        assistant_message_id: str | None,
    ) -> None:
        project = self.db.get(Project, project_id) if project_id else None
        if project_id and (not project or project.user_id != user_id):
            raise DurableToolRuntimeError("project_not_found", "项目不存在。")

        conversation = self.db.get(Conversation, conversation_id) if conversation_id else None
        if conversation_id and (not conversation or conversation.user_id != user_id):
            raise DurableToolRuntimeError("conversation_not_found", "会话不存在。")
        if project_id and conversation and conversation.project_id != project_id:
            raise DurableToolRuntimeError("scope_mismatch", "会话不属于指定项目。")

        if assistant_message_id and not conversation_id:
            raise DurableToolRuntimeError("scope_mismatch", "关联 assistant message 时必须同时提供 conversation_id。")
        if assistant_message_id:
            message = self.db.get(Message, assistant_message_id)
            if (
                not message
                or message.conversation_id != conversation_id
                or message.role != "assistant"
            ):
                raise DurableToolRuntimeError("assistant_message_not_found", "Assistant 消息不存在。")

    @staticmethod
    def _request_hash(run: AgentRun) -> str | None:
        try:
            state = json.loads(run.planner_state_json or "{}")
        except json.JSONDecodeError:
            return None
        value = state.get("request_hash") if isinstance(state, dict) else None
        return str(value) if value else None

    def claim_next(self, *, owner: str, lease_seconds: int | None = None) -> DurableStepClaim | None:
        now = utcnow()
        ttl = max(15, int(lease_seconds or self.LEASE_SECONDS))
        event = self.db.scalars(
            select(AgentOutboxEvent)
            .join(AgentStep, AgentStep.id == AgentOutboxEvent.step_id)
            .join(AgentRun, AgentRun.id == AgentOutboxEvent.run_id)
            .where(
                AgentRun.runtime_kind == "durable_tool_workflow",
                AgentRun.status.in_(["queued", "running"]),
                or_(
                    AgentOutboxEvent.status == "pending",
                    and_(AgentOutboxEvent.status == "running", AgentOutboxEvent.lease_expires_at < now),
                ),
                or_(AgentOutboxEvent.available_at.is_(None), AgentOutboxEvent.available_at <= now),
            )
            .order_by(AgentOutboxEvent.created_at.asc(), AgentStep.sequence.asc())
            .with_for_update(skip_locked=True)
            .limit(1)
        ).first()
        if not event:
            return None
        step = self.db.get(AgentStep, event.step_id)
        run = self.db.get(AgentRun, event.run_id)
        if not step or not run:
            event.status = "dead_letter"
            event.error_code = "runtime_state_missing"
            event.dead_lettered_at = now
            self.db.commit()
            return None
        event.status = "running"
        event.lease_owner = owner
        event.lease_version = int(event.lease_version or 0) + 1
        event.lease_expires_at = now + timedelta(seconds=ttl)
        event.heartbeat_at = now
        event.attempt_count = int(event.attempt_count or 0) + 1
        step.status = "running"
        step.lease_owner = owner
        step.lease_version = event.lease_version
        step.lease_expires_at = event.lease_expires_at
        step.heartbeat_at = now
        step.started_at = step.started_at or now
        run.status = "running"
        run.current_step = max(int(run.current_step or 0), int(step.sequence))
        self.db.commit()
        return DurableStepClaim(event.id, event.lease_version, step.id)

    def renew_claim(
        self,
        *,
        claim: DurableStepClaim,
        owner: str,
        lease_seconds: int | None = None,
    ) -> bool:
        """Extend an active lease without changing the durable state version."""

        now = utcnow()
        ttl = max(15, int(lease_seconds or self.LEASE_SECONDS))
        event = self.db.scalars(
            select(AgentOutboxEvent)
            .where(AgentOutboxEvent.id == claim.outbox_event_id)
            .with_for_update()
            .limit(1)
        ).first()
        if (
            not event
            or event.status != "running"
            or event.lease_owner != owner
            or event.lease_version != claim.outbox_lease_version
        ):
            self.db.rollback()
            return False
        step = self.db.scalars(
            select(AgentStep).where(AgentStep.id == claim.step_id).with_for_update().limit(1)
        ).first()
        if (
            not step
            or step.status != "running"
            or step.lease_owner != owner
            or step.lease_version != claim.outbox_lease_version
        ):
            self.db.rollback()
            return False
        expires_at = now + timedelta(seconds=ttl)
        event.heartbeat_at = now
        event.lease_expires_at = expires_at
        step.heartbeat_at = now
        step.lease_expires_at = expires_at
        self.db.commit()
        return True

    def get_run_snapshot(self, *, run_id: str, user_id: str) -> dict[str, Any] | None:
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
            return None
        return {
            "run": run,
            "steps": list(self.db.scalars(select(AgentStep).where(AgentStep.run_id == run.id).order_by(AgentStep.sequence)).all()),
            "checkpoint": self.db.scalars(
                select(AgentCheckpoint).where(AgentCheckpoint.run_id == run.id).order_by(AgentCheckpoint.state_version.desc()).limit(1)
            ).first(),
            "artifacts": list(self.db.scalars(select(AgentArtifact).where(AgentArtifact.run_id == run.id).order_by(AgentArtifact.created_at)).all()),
            "outbox_events": list(self.db.scalars(select(AgentOutboxEvent).where(AgentOutboxEvent.run_id == run.id).order_by(AgentOutboxEvent.created_at)).all()),
        }

    def metrics(self, *, user_id: str) -> dict[str, Any]:
        def grouped(model: Any, column: Any) -> dict[str, int]:
            rows = self.db.execute(
                select(column, func.count()).join(AgentRun, AgentRun.id == model.run_id).where(AgentRun.user_id == user_id).group_by(column)
            ).all()
            return {str(status): int(count) for status, count in rows}

        chat_metrics = list(
            self.db.scalars(
                select(ChatRuntimeMetric)
                .where(ChatRuntimeMetric.user_id == user_id)
                .order_by(ChatRuntimeMetric.created_at.desc())
                .limit(500)
            ).all()
        )
        parsed_stats: list[dict[str, Any]] = []
        for metric in chat_metrics:
            try:
                value = json.loads(metric.stats_json or "{}")
            except json.JSONDecodeError:
                value = {}
            parsed_stats.append(value if isinstance(value, dict) else {})

        tool_status_rows = self.db.execute(
            select(ToolCallRun.status, func.count())
            .join(ToolRouteRun, ToolRouteRun.id == ToolCallRun.route_run_id)
            .where(ToolRouteRun.user_id == user_id)
            .group_by(ToolCallRun.status)
        ).all()
        rag_logs = list(
            self.db.scalars(
                select(KnowledgeRetrievalLog)
                .where(KnowledgeRetrievalLog.user_id == user_id)
                .order_by(KnowledgeRetrievalLog.created_at.desc())
                .limit(500)
            ).all()
        )
        rag_retrieved = 0
        rag_injected = 0
        for log in rag_logs:
            try:
                diagnostics = json.loads(log.diagnostics_json or "{}")
            except json.JSONDecodeError:
                diagnostics = {}
            if isinstance(diagnostics, dict):
                rag_retrieved += int(diagnostics.get("knowledge_chunks_retrieved", 0) or 0)
                rag_injected += int(diagnostics.get("knowledge_chunks_injected", 0) or 0)

        def sum_stat(key: str) -> int:
            return sum(int(item.get(key, 0) or 0) for item in parsed_stats)

        return {
            "observation_window": {
                "chat_runtime_metrics_max_records": 500,
                "knowledge_retrieval_logs_max_records": 500,
            },
            "runs_by_status": {
                str(status): int(count)
                for status, count in self.db.execute(
                    select(AgentRun.status, func.count()).where(AgentRun.user_id == user_id).group_by(AgentRun.status)
                ).all()
            },
            "steps_by_status": grouped(AgentStep, AgentStep.status),
            "outbox_by_status": grouped(AgentOutboxEvent, AgentOutboxEvent.status),
            "artifacts_total": int(
                self.db.scalar(select(func.count()).select_from(AgentArtifact).where(AgentArtifact.user_id == user_id)) or 0
            ),
            "tool_calls_by_status": {str(status): int(count) for status, count in tool_status_rows},
            "rag": {
                "retrieval_runs": len(rag_logs),
                "chunks_retrieved": rag_retrieved,
                "chunks_injected": rag_injected,
            },
            "chat": {
                "runs_observed": len(chat_metrics),
                "provider_input_tokens": sum_stat("provider_input_tokens"),
                "provider_output_tokens": sum_stat("provider_output_tokens"),
                "provider_cached_input_tokens": sum_stat("provider_cached_input_tokens"),
                "external_sources_retrieved": sum_stat("external_sources_total"),
                "external_sources_injected": sum_stat("external_sources_included"),
                "knowledge_chunks_retrieved": sum_stat("knowledge_chunks_retrieved"),
                "knowledge_chunks_injected": sum_stat("knowledge_chunks_injected"),
            },
        }

    def _checkpoint(self, run: AgentRun, *, step: AgentStep | None, observations: list[dict[str, Any]]) -> None:
        self.db.add(
            AgentCheckpoint(
                run_id=run.id,
                step_sequence=step.sequence if step else run.current_step,
                state_version=run.state_version,
                planner_state_json=run.planner_state_json,
                observations_json=self._json(observations),
                remaining_budget_json=self._json({"remaining_steps": max(0, run.max_steps - run.current_step)}),
            )
        )


class DurableToolWorker:
    """A small worker that can be run repeatedly or under a process supervisor."""

    HEARTBEAT_SECONDS = 30

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session],
        owner: str | None = None,
        executor_factory: Callable[..., ToolExecutor] = ToolExecutor,
    ) -> None:
        self.session_factory = session_factory
        self.owner = owner or f"agent-worker:{socket.gethostname()}"
        self.executor_factory = executor_factory

    async def run_once(self) -> bool:
        with self.session_factory() as db:
            claim = DurableToolRunService(db).claim_next(owner=self.owner)
        if not claim:
            return False
        with self.session_factory() as db:
            await self._execute_claim(db, claim)
        return True

    async def run_forever(self, *, poll_interval_seconds: float = 1.0) -> None:
        """Keep polling after an empty queue and isolate transient loop failures."""

        delay = max(0.1, float(poll_interval_seconds))
        while True:
            try:
                worked = await self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("durable Tool worker loop failed")
                worked = False
            if not worked:
                await asyncio.sleep(delay)

    async def _execute_claim(self, db: Session, claim: DurableStepClaim) -> None:
        event = db.get(AgentOutboxEvent, claim.outbox_event_id)
        step = db.get(AgentStep, claim.step_id)
        if not event or not step:
            return
        run = db.get(AgentRun, step.run_id)
        if not run or event.lease_owner != self.owner or event.lease_version != claim.outbox_lease_version:
            return
        dependency_status = self._dependency_status(db, run.id, step)
        if dependency_status == "waiting":
            self._requeue(
                db,
                run,
                step,
                event,
                claim,
                delay_seconds=2,
                error_code="dependencies_pending",
                error_message="等待依赖 Step 完成。",
                record_checkpoint=False,
            )
            return
        if dependency_status == "blocked":
            self._finish_terminal(
                db, run, step, event, claim, status="skipped", error_code="dependency_failed", error_message="依赖 Step 未成功，当前 Step 不执行。"
            )
            return
        try:
            call = self._build_call(db, run, step)
        except DurableToolRuntimeError as exc:
            self._finish_terminal(db, run, step, event, claim, status="skipped", error_code=exc.code, error_message=str(exc))
            return

        # Persist attempt ownership before issuing an external request. A dependency
        # wait is not an execution attempt, while a crash after this point must be
        # retried under a new lease and remains safe because this runtime is read-only.
        if not self._lock_claim(db, run, step, event, claim):
            return
        step.attempts = int(step.attempts or 0) + 1
        step.heartbeat_at = utcnow()
        db.commit()

        executor = self.executor_factory(
            db=db,
            user_id=run.user_id,
            project_id=run.project_id,
            conversation_id=run.conversation_id,
            assistant_message_id=run.assistant_message_id,
            catalog=ToolCatalog(db=db, user_id=run.user_id),
        )
        result, events = await self._execute_with_heartbeat(executor, call, claim)
        if result.status == "success":
            payload = {
                "call_id": call.call_id,
                "tool_key": call.tool_key,
                "sources": [source.to_public_dict() for source in result.sources],
                "events": [event_item.to_public_dict() for event_item in events],
                "elapsed_ms": result.elapsed_ms,
            }
            self._finish_success(db, run, step, event, claim, payload)
            return
        if result.status in {"skipped", "confirmation_required"}:
            self._finish_terminal(
                db,
                run,
                step,
                event,
                claim,
                status="skipped",
                error_code="tool_not_executable",
                error_message=(result.error_message or "工具不满足可执行条件。")[:500],
            )
            return
        error_message = (result.error_message or "工具调用失败，请稍后重试。")[:500]
        if not result.retryable:
            self._finish_terminal(
                db,
                run,
                step,
                event,
                claim,
                status="failed",
                error_code="permanent_tool_error",
                error_message=error_message,
            )
            return
        self._retry_or_dlq(db, run, step, event, claim, error_message)

    async def _execute_with_heartbeat(
        self,
        executor: ToolExecutor,
        call: PlannedToolCall,
        claim: DurableStepClaim,
    ):
        stop = asyncio.Event()
        heartbeat = asyncio.create_task(self._heartbeat_loop(claim, stop))
        try:
            return await executor.execute(call)
        finally:
            stop.set()
            await heartbeat

    async def _heartbeat_loop(self, claim: DurableStepClaim, stop: asyncio.Event) -> None:
        while True:
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.HEARTBEAT_SECONDS)
                return
            except TimeoutError:
                try:
                    with self.session_factory() as heartbeat_db:
                        renewed = DurableToolRunService(heartbeat_db).renew_claim(
                            claim=claim,
                            owner=self.owner,
                        )
                except Exception:
                    logger.exception("durable Tool lease heartbeat failed")
                    return
                if not renewed:
                    return

    def _build_call(self, db: Session, run: AgentRun, step: AgentStep) -> PlannedToolCall:
        catalog = ToolCatalog(db=db, user_id=run.user_id)
        definition = catalog.get_or_none(step.tool_key)
        if not definition or not definition.read_only or definition.risk_level != "low":
            raise DurableToolRuntimeError("unsafe_or_missing_tool", "工具已失效或不再满足只读低风险约束。")
        try:
            arguments = json.loads(step.arguments_json or "{}")
            bindings = json.loads(step.result_bindings_json or "[]")
        except json.JSONDecodeError as exc:
            raise DurableToolRuntimeError("invalid_persisted_state", "持久化 Tool Step 格式损坏。") from exc
        if not isinstance(arguments, dict) or not isinstance(bindings, list):
            raise DurableToolRuntimeError("invalid_persisted_state", "持久化 Tool Step 格式损坏。")
        arguments = self._resolve_bindings(db, run, step, arguments, bindings)
        try:
            arguments = ToolSchemaValidator().validate(definition=definition, arguments=arguments)
        except ToolSchemaValidationError as exc:
            raise DurableToolRuntimeError("schema_invalid", str(exc)) from exc
        return PlannedToolCall(
            call_id=step.call_id,
            tool_key=definition.tool_key,
            provider=definition.provider,
            category=definition.category,
            display_name=definition.display_name,
            confidence=1.0,
            reason="durable_tool_workflow",
            arguments=arguments,
            depends_on=json.loads(step.depends_on_json or "[]"),
            can_parallel=False,
            result_bindings=[ToolResultBinding(**binding) for binding in bindings],
        )

    def _resolve_bindings(self, db: Session, run: AgentRun, step: AgentStep, arguments: dict[str, Any], bindings: list[dict[str, Any]]) -> dict[str, Any]:
        resolved = dict(arguments)
        resolver = ToolResultBindingResolver()
        for raw in bindings:
            try:
                binding = ToolResultBinding(**raw)
                resolver.validate_declaration(binding)
            except (TypeError, ToolResultBindingError) as exc:
                raise DurableToolRuntimeError("invalid_bindings", "结果绑定格式非法。") from exc
            source_step = db.scalars(
                select(AgentStep).where(AgentStep.run_id == run.id, AgentStep.call_id == binding.source_call_id).limit(1)
            ).first()
            artifact = (
                db.scalars(select(AgentArtifact).where(AgentArtifact.step_id == source_step.id).order_by(AgentArtifact.created_at).limit(1)).first()
                if source_step
                else None
            )
            if not artifact:
                if binding.required:
                    raise DurableToolRuntimeError("required_binding_missing", f"未找到 {binding.target_argument} 所需的上游结果。")
                continue
            try:
                value = resolver.resolve_bound_value(json.loads(artifact.content_json), binding.source_path)
            except (json.JSONDecodeError, ToolResultBindingError) as exc:
                if binding.required:
                    raise DurableToolRuntimeError(
                        "required_binding_missing",
                        f"未找到 {binding.target_argument} 所需的上游结果。",
                    ) from exc
                continue
            resolved[binding.target_argument] = value
        return resolved

    @staticmethod
    def _dependency_status(db: Session, run_id: str, step: AgentStep) -> str:
        dependencies = json.loads(step.depends_on_json or "[]")
        if not dependencies:
            return "ready"
        upstream = list(db.scalars(select(AgentStep).where(AgentStep.run_id == run_id, AgentStep.call_id.in_(dependencies))).all())
        by_call = {item.call_id: item.status for item in upstream}
        if len(by_call) != len(dependencies) or any(status in {"pending", "running", "waiting_approval"} for status in by_call.values()):
            return "waiting"
        if any(status != "succeeded" for status in by_call.values()):
            return "blocked"
        return "ready"

    def _finish_success(self, db: Session, run: AgentRun, step: AgentStep, event: AgentOutboxEvent, claim: DurableStepClaim, payload: dict[str, Any]) -> None:
        if not self._lock_claim(db, run, step, event, claim):
            return
        content_json = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        preview = self._preview(payload)
        artifact = AgentArtifact(
            run_id=run.id,
            step_id=step.id,
            user_id=run.user_id,
            content_hash=hashlib.sha256(content_json.encode("utf-8")).hexdigest(),
            preview=preview,
            content_json=content_json,
            char_count=len(content_json),
        )
        db.add(artifact)
        db.flush()
        step.status = "succeeded"
        step.result_json = json.dumps({"artifact_id": artifact.id, "sources_count": len(payload["sources"])}, ensure_ascii=False)
        step.finished_at = utcnow()
        step.lease_owner = None
        step.lease_expires_at = None
        event.status = "succeeded"
        event.lease_owner = None
        event.lease_expires_at = None
        run.state_version += 1
        self._update_run_terminal_state(db, run)
        DurableToolRunService(db)._checkpoint(run, step=step, observations=[{"type": "step_succeeded", "artifact_id": artifact.id}])
        db.commit()

    def _finish_terminal(self, db: Session, run: AgentRun, step: AgentStep, event: AgentOutboxEvent, claim: DurableStepClaim, *, status: str, error_code: str, error_message: str) -> None:
        if not self._lock_claim(db, run, step, event, claim):
            return
        step.status = status
        step.error_code = error_code
        step.error_message = error_message
        step.finished_at = utcnow()
        step.lease_owner = None
        step.lease_expires_at = None
        event.status = "succeeded" if status == "skipped" else status
        event.error_code = error_code
        event.error_message = error_message
        event.lease_owner = None
        event.lease_expires_at = None
        run.state_version += 1
        self._update_run_terminal_state(db, run)
        DurableToolRunService(db)._checkpoint(run, step=step, observations=[{"type": f"step_{status}", "error_code": error_code}])
        db.commit()

    def _retry_or_dlq(self, db: Session, run: AgentRun, step: AgentStep, event: AgentOutboxEvent, claim: DurableStepClaim, message: str) -> None:
        if not self._lock_claim(db, run, step, event, claim):
            return
        now = utcnow()
        if step.attempts >= step.max_attempts:
            step.status = "dead_letter"
            step.error_code = "max_attempts_exhausted"
            step.error_message = message
            step.finished_at = now
            step.dead_lettered_at = now
            step.lease_owner = None
            step.lease_expires_at = None
            event.status = "dead_letter"
            event.error_code = "max_attempts_exhausted"
            event.error_message = message
            event.dead_lettered_at = now
            event.lease_owner = None
            event.lease_expires_at = None
            run.state_version += 1
            self._update_run_terminal_state(db, run)
            DurableToolRunService(db)._checkpoint(run, step=step, observations=[{"type": "step_dead_letter", "error": message}])
            db.commit()
            return
        delay = min(60, 2 ** max(0, step.attempts - 1))
        self._requeue(db, run, step, event, claim, delay_seconds=delay, error_code="retryable_tool_error", error_message=message)

    def _requeue(
        self,
        db: Session,
        run: AgentRun,
        step: AgentStep,
        event: AgentOutboxEvent,
        claim: DurableStepClaim,
        *,
        delay_seconds: int,
        error_code: str,
        error_message: str,
        record_checkpoint: bool = True,
    ) -> None:
        if not self._lock_claim(db, run, step, event, claim):
            return
        available_at = utcnow() + timedelta(seconds=delay_seconds)
        step.status = "pending"
        step.available_at = available_at
        step.error_code = error_code
        step.error_message = error_message
        step.lease_owner = None
        step.lease_expires_at = None
        event.status = "pending"
        event.available_at = available_at
        event.error_code = error_code
        event.error_message = error_message
        event.lease_owner = None
        event.lease_expires_at = None
        self._update_run_terminal_state(db, run)
        if record_checkpoint:
            run.state_version += 1
            DurableToolRunService(db)._checkpoint(run, step=step, observations=[{"type": "step_requeued", "delay_seconds": delay_seconds, "error_code": error_code}])
        db.commit()

    def _lock_claim(
        self,
        db: Session,
        run: AgentRun,
        step: AgentStep,
        event: AgentOutboxEvent,
        claim: DurableStepClaim,
    ) -> bool:
        """Fence completion against a lease reclaimed while the Tool was running."""

        db.expire_all()
        locked_event = db.scalars(
            select(AgentOutboxEvent)
            .where(AgentOutboxEvent.id == claim.outbox_event_id)
            .with_for_update()
            .limit(1)
        ).first()
        if (
            not locked_event
            or locked_event.status != "running"
            or locked_event.lease_owner != self.owner
            or locked_event.lease_version != claim.outbox_lease_version
        ):
            db.rollback()
            return False
        db.refresh(step)
        locked_run = db.scalars(
            select(AgentRun)
            .where(AgentRun.id == run.id)
            .with_for_update()
            .limit(1)
        ).first()
        if not locked_run:
            db.rollback()
            return False
        if (
            step.status != "running"
            or step.lease_owner != self.owner
            or step.lease_version != claim.outbox_lease_version
        ):
            db.rollback()
            return False
        # SQLAlchemy's identity map returns the same instance after the locked
        # SELECT; assigning it keeps callers explicit and prevents stale fields.
        if locked_event is not event:
            db.refresh(event)
        if locked_run is not run:
            db.refresh(run)
        return True

    @staticmethod
    def _preview(payload: dict[str, Any]) -> str:
        sources = payload.get("sources") or []
        lines = [str(item.get("title") or "Tool result") + ": " + str(item.get("display_text") or "") for item in sources if isinstance(item, dict)]
        return "\n".join(lines)[:4000] or "Tool returned no displayable sources."

    @staticmethod
    def _update_run_terminal_state(db: Session, run: AgentRun) -> None:
        statuses = list(db.scalars(select(AgentStep.status).where(AgentStep.run_id == run.id)).all())
        terminal_statuses = {"succeeded", "skipped", "cancelled", "failed", "dead_letter"}
        if statuses and all(status in terminal_statuses for status in statuses):
            if any(status == "dead_letter" for status in statuses):
                run.status = "dead_letter"
            elif all(status == "succeeded" for status in statuses):
                run.status = "succeeded"
            else:
                run.status = "failed"
            run.finished_at = utcnow()
        else:
            run.status = "running" if any(status in {"running", "waiting_approval"} for status in statuses) else "queued"
            run.finished_at = None
