"""Chat任务控制面：短事务创建、领取、正文/事件提交和取消裁决。"""

import asyncio
import json
from dataclasses import dataclass
from datetime import timedelta
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import delete, func, or_, select, update

from app.core.config import settings
from app.models.chat_run import ChatRun, ChatRunEvent
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.observability import ChatRuntimeMetric
from app.repositories.message_repo import MessageGenerationConflict
from app.services.chat_execution_service import ChatExecutionService
from app.services.resource_quota_service import exhausted, quota_lock
from app.services.runtime_lease import aware, owns_live_lease, utcnow

ACTIVE = ("queued", "running", "cancel_requested")
TERMINAL = ("succeeded", "cancelled", "failed", "expired", "interrupted")
LEASE_SECONDS = 45
MAX_WORKER_ATTEMPTS = 3
MAX_GENERATION_ATTEMPTS = 2


@dataclass(frozen=True)
class ChatClaim:
    run_id: str
    owner: str
    version: int


class ChatLeaseLost(asyncio.CancelledError):
    """保留为独立控制异常，防止被模型/工具普通失败兜底吞掉。"""


class ChatRunService:
    def __init__(self, db):
        self.db = db

    def create(self, *, user, payload, request_id=None, kind="new"):
        request_id = request_id or str(uuid4())
        if not 1 <= len(request_id) <= 128:
            raise HTTPException(422, "请求标识长度不合法")
        request = {"kind": kind, "payload": payload.model_dump(mode="json", exclude={"client_request_id"})}
        encoded = json.dumps(request, ensure_ascii=False, sort_keys=True)
        service = ChatExecutionService(db=self.db, current_user=user)
        # 默认设置可能首次创建并提交，必须先完成，再进入消息/Run原子事务。
        defaults = service.setting_service.get_or_create_user_settings(user.id) if kind == "new" else None
        quota_lock(self.db, user.id)
        previous = self.db.scalar(select(ChatRun).where(ChatRun.user_id == user.id,
            ChatRun.client_request_id == request_id))
        if previous:
            if previous.request_json != encoded:
                raise HTTPException(409, "相同请求标识不能用于不同任务")
            return previous
        count = self.db.scalar(select(func.count()).select_from(ChatRun).where(
            ChatRun.user_id == user.id, ChatRun.status.in_(ACTIVE))) or 0
        if count >= settings.user_chat_concurrency:
            raise exhausted("当前账号后台回答已达上限，请等待或停止已有任务")
        try:
            if kind == "new":
                turn = service.turn_bootstrapper._bootstrap_new_turn_uncommitted(
                    payload=payload, default_settings=defaults)
                conversation, user_message, assistant = turn.conversation, turn.user_message, turn.assistant_message
            else:
                conversation = service.conversation_repo.get_by_user(payload.conversation_id, user.id)
                if not conversation:
                    raise HTTPException(404, "会话不存在")
                service.message_repo.lock_conversation(conversation.id)
                rows = service.message_repo.list_for_context(conversation)
                assistant = rows[-1] if rows else None
                if not assistant or assistant.role != "assistant" or assistant.id != payload.assistant_message_id:
                    raise HTTPException(400, "仅支持最后一轮回答")
                user_message = service.message_repo.latest_user_before(assistant)
                if not user_message or (kind == "edit" and user_message.id != payload.user_message_id):
                    raise HTTPException(400, "未找到最后一轮用户消息")
                if kind == "edit":
                    service.validate_attachment_context_inputs(payload.attachments or [])
                    service.message_service.edit_and_reset_for_regeneration(user_message=user_message,
                        assistant_message=assistant, content=payload.content, uploads=payload.attachments,
                        user_id=user.id, commit=False)
                else:
                    service.message_service.reset_assistant_for_regeneration(assistant, commit=False)
            self.db.flush()
            run = ChatRun(user_id=user.id, conversation_id=conversation.id, user_message_id=user_message.id,
                assistant_message_id=assistant.id, generation_id=assistant.generation_id,
                client_request_id=request_id, request_json=encoded, status="queued", phase="queued",
                max_output_tokens=settings.chat_run_max_output_tokens,
                queued_expires_at=utcnow() + timedelta(seconds=settings.chat_run_queue_seconds))
            self.db.add(run)
            self.db.flush()
            self._event(run, {"type": "run_status", "status": "queued", "run_id": run.id})
            self.db.commit()
            self.db.refresh(run)
            return run
        except MessageGenerationConflict as exc:
            self.db.rollback()
            raise HTTPException(409, str(exc)) from exc
        except Exception:
            self.db.rollback()
            raise

    def owned(self, run_id, user_id, *, lock=False):
        query = select(ChatRun).join(Conversation, Conversation.id == ChatRun.conversation_id).where(
            ChatRun.id == run_id, ChatRun.user_id == user_id, Conversation.user_id == user_id)
        if lock:
            query = query.with_for_update(of=ChatRun)
        run = self.db.scalar(query.execution_options(populate_existing=True))
        if not run:
            raise HTTPException(404, "任务不存在")
        return run

    @staticmethod
    def snapshot(run):
        return {"id": run.id, "conversation_id": run.conversation_id,
            "assistant_message_id": run.assistant_message_id, "user_message_id": run.user_message_id,
            "status": run.status, "phase": run.phase, "content": run.content,
            "reasoning_content": run.reasoning_content, "external_sources": json.loads(run.sources_json),
            "context_info": json.loads(run.context_json),
            "citation_audit": json.loads(run.citation_audit_json) if run.citation_audit_json else None,
            "last_event_id": run.event_sequence, "events_pruned_through": run.events_pruned_through,
            "model_attempts": run.model_attempts, "error_code": run.error_code,
            "created_at": run.created_at.isoformat(),
            "finished_at": run.finished_at.isoformat() if run.finished_at else None}

    def _event(self, run, event):
        encoded = json.dumps(event, ensure_ascii=False, default=str)
        run.event_sequence += 1
        run.event_bytes += len(encoded.encode("utf-8"))
        self.db.add(ChatRunEvent(run_id=run.id, sequence=run.event_sequence, event_json=encoded))

    def cancel(self, run_id, user_id):
        run = self.owned(run_id, user_id, lock=True)
        if run.status not in TERMINAL and run.status != "cancel_requested":
            run.cancel_requested_at = utcnow()
            if run.status == "queued":
                self._terminal(run, "cancelled", "user_cancelled")
            else:
                run.status = "cancel_requested"
                self._event(run, {"type": "run_status", "status": run.status, "run_id": run.id})
            self.db.commit()
        return self.snapshot(run)

    def _locked_claim(self, claim, *, allow_cancel=False):
        run = self.db.scalar(select(ChatRun).where(ChatRun.id == claim.run_id).with_for_update()
                             .execution_options(populate_existing=True))
        allowed = ("running", "cancel_requested") if allow_cancel else ("running",)
        if not run or run.status not in allowed or not owns_live_lease(run, owner=claim.owner, version=claim.version):
            self.db.rollback()
            return None
        message = self.db.get(Message, run.assistant_message_id, populate_existing=True)
        if not message or message.generation_id != run.generation_id:
            self._terminal(run, "interrupted", "generation_superseded", write_message=False)
            self.db.commit()
            return None
        return run

    def claim_next(self, owner):
        quota_lock(self.db, "__chat_claim_global__")
        self.reconcile(commit=False)
        # Session关闭autoflush；过期收口必须先进入SQL，下面的领取查询才看不到旧queued。
        self.db.flush()
        now = utcnow()
        running = self.db.scalar(select(func.count()).select_from(ChatRun).where(ChatRun.status.in_(
            ("running", "cancel_requested")))) or 0
        if running >= settings.chat_run_global_concurrency:
            self.db.commit()
            return None
        run = self.db.scalar(select(ChatRun).where(ChatRun.status == "queued").order_by(ChatRun.created_at)
                             .with_for_update(skip_locked=True).limit(1))
        if not run:
            self.db.commit()
            return None
        run.status, run.phase = "running", "prepare"
        run.lease_owner, run.lease_version = owner, run.lease_version + 1
        run.heartbeat_at = now
        run.lease_expires_at = now + timedelta(seconds=LEASE_SECONDS)
        # 接管不能重置任务总时长，否则每次重启都可以无限续命。
        run.deadline_at = run.deadline_at or now + timedelta(seconds=settings.chat_run_total_seconds)
        from app.services.chat_tool_runtime import ensure_tool_run
        _, coordinator = ensure_tool_run(self.db, run, self.db.get(Conversation, run.conversation_id))
        if coordinator.status != "succeeded":
            coordinator.lease_owner, coordinator.lease_version = owner, run.lease_version
            coordinator.lease_expires_at, coordinator.heartbeat_at = run.lease_expires_at, now
        self._event(run, {"type": "run_status", "status": "running", "run_id": run.id})
        claim = ChatClaim(run.id, owner, run.lease_version)
        self.db.commit()
        return claim

    def heartbeat(self, claim):
        run = self._locked_claim(claim, allow_cancel=True)
        if not run:
            return False
        if run.status == "cancel_requested":
            self.db.rollback()
            return False
        if run.deadline_at and aware(run.deadline_at) <= utcnow():
            self.db.rollback()
            return False
        run.heartbeat_at = utcnow()
        run.lease_expires_at = utcnow() + timedelta(seconds=LEASE_SECONDS)
        from app.models.agent_runtime import AgentRun, AgentStep
        # Chat工具Step服从父Run执行权，不再创建另一组独立心跳；投影当前有效租约便于诊断。
        self.db.execute(update(AgentStep).where(AgentStep.run_id.in_(select(AgentRun.id).where(
            AgentRun.chat_run_id == run.id)), AgentStep.status == "running",
            AgentStep.lease_owner == claim.owner, AgentStep.lease_version == claim.version).values(
                heartbeat_at=run.heartbeat_at, lease_expires_at=run.lease_expires_at))
        self.db.commit()
        return True

    def start_generation(self, claim, *, uses_model=True):
        run = self._locked_claim(claim)
        if not run:
            return False
        # 最多两次最终生成；仅已建立恢复记录的任务可以走Worker接管路径。
        if run.model_attempts >= MAX_GENERATION_ATTEMPTS:
            self.db.rollback()
            return False
        if run.model_attempts:
            from app.services.chat_tool_runtime import archive_partial_generation
            archive_partial_generation(self.db, run)
            run.content, run.reasoning_content, run.sources_json = "", "", "[]"
            run.citation_audit_json = None
            self._message(run, "streaming")
            # 旧事件仍可审计；订阅者收到此快照时替换当前回答，而不是拼接两次生成。
            self._event(run, {"type": "run_snapshot", "run": self.snapshot(run), "reason": "generation_restarted"})
        run.model_attempts += 1 if uses_model else 0
        run.phase = "generate"
        self._event(run, {"type": "run_phase", "phase": "generate"})
        self.db.commit()
        return True

    def append(self, claim, events):
        run = self._locked_claim(claim)
        if not run:
            return False
        for event in events:
            kind = event.get("type")
            if kind == "answer_delta":
                run.content += str(event.get("text") or "")
            elif kind == "reasoning_delta":
                run.reasoning_content += str(event.get("text") or "")
            elif kind == "context_info":
                run.context_json = json.dumps(event.get("info") or {}, ensure_ascii=False, default=str)
            elif kind == "tool_sources":
                run.sources_json = json.dumps(event.get("sources") or [], ensure_ascii=False, default=str)
            self._event(run, event)
        if len(run.content) + len(run.reasoning_content) > settings.chat_run_max_chars or run.event_bytes > settings.chat_run_event_max_bytes:
            self.db.rollback()
            raise ValueError("chat_output_budget_exhausted")
        self._message(run, "streaming")
        self.db.commit()
        return True

    def finish(self, claim, *, status, content=None, reasoning=None, sources=None, audit=None,
               error_code=None, metric=None):
        run = self._locked_claim(claim, allow_cancel=True)
        if not run:
            return False
        # 锁先观察到取消，完成也必须收口为cancelled；完成先提交则取消只读返回。
        if run.status == "cancel_requested":
            status, error_code = "cancelled", "user_cancelled"
            # 取消提交后拒绝接受新正文；仅保留取消前已落库的片段。
            content = reasoning = sources = audit = None
        elif run.deadline_at and aware(run.deadline_at) <= utcnow():
            status, error_code = "expired", "run_total_timeout"
        if content is not None:
            run.content = content[:settings.chat_run_max_chars]
        if reasoning is not None:
            run.reasoning_content = reasoning[:max(0, settings.chat_run_max_chars - len(run.content))]
        if sources is not None:
            run.sources_json = json.dumps(sources, ensure_ascii=False)
        if audit is not None:
            run.citation_audit_json = json.dumps(audit, ensure_ascii=False)
            self._event(run, {"type": "citation_audit", "audit": audit})
        self._terminal(run, status, error_code)
        if metric:
            row = self.db.scalar(select(ChatRuntimeMetric).where(ChatRuntimeMetric.assistant_message_id == run.assistant_message_id))
            if not row:
                row = ChatRuntimeMetric(user_id=run.user_id, conversation_id=run.conversation_id,
                    assistant_message_id=run.assistant_message_id, provider_type=metric["provider_type"],
                    model_name=metric["model_name"], status=self._message_status(status))
                self.db.add(row)
            row.provider_type, row.model_name = metric["provider_type"], metric["model_name"]
            row.status, row.stats_json = self._message_status(status), json.dumps(metric["stats"], ensure_ascii=False, default=str)
        self.db.commit()
        return True

    @staticmethod
    def _message_status(status):
        return "done" if status == "succeeded" else "cancelled" if status == "cancelled" else "failed"

    def _message(self, run, status):
        message = self.db.scalar(select(Message).where(Message.id == run.assistant_message_id,
            Message.generation_id == run.generation_id).with_for_update().execution_options(populate_existing=True))
        if message:
            message.content, message.reasoning_content = run.content, run.reasoning_content or None
            message.external_sources, message.citation_audit = run.sources_json, run.citation_audit_json
            message.status, message.updated_at = status, utcnow()

    def _terminal(self, run, status, error_code, *, write_message=True):
        run.status, run.phase, run.error_code = status, "terminal", error_code
        run.finished_at, run.lease_owner, run.lease_expires_at = utcnow(), None, None
        from app.services.chat_tool_runtime import close_tool_run
        close_tool_run(self.db, run, status)
        if write_message:
            self._message(run, self._message_status(status))
        self._event(run, {"type": "run_status", "status": status, "run_id": run.id, "error_code": error_code})
        if status == "succeeded":
            self._event(run, {"type": "done", "assistant_message_id": run.assistant_message_id})
        elif status != "cancelled":
            self._event(run, {"type": "model_error", "error_code": error_code,
                "error": "回答生成已中断，已保留收到的内容。可查看详情或重新生成。",
                "assistant_message_id": run.assistant_message_id})

    def reconcile(self, *, commit=True):
        now = utcnow()
        runs = list(self.db.scalars(select(ChatRun).where(or_(
            (ChatRun.status == "queued") & (ChatRun.queued_expires_at <= now),
            ChatRun.status.in_(("running", "cancel_requested")) & (ChatRun.lease_expires_at <= now),
        )).with_for_update(skip_locked=True).limit(100)))
        for run in runs:
            if run.status == "queued":
                self._terminal(run, "expired", "queue_timeout")
            elif run.status == "cancel_requested":
                self._terminal(run, "cancelled", "user_cancelled")
            elif self._can_recover(run):
                self._requeue(run, "worker_lease_expired")
            else:
                self._terminal(run, "interrupted", "worker_lease_expired")
        if commit:
            self.db.commit()
        return len(runs)

    def _can_recover(self, run):
        from app.models.agent_runtime import AgentRun
        from app.services.chat_tool_runtime import ChatToolPersistence, VERSION
        if run.lease_version >= MAX_WORKER_ATTEMPTS or run.model_attempts >= MAX_GENERATION_ATTEMPTS or not run.deadline_at or aware(run.deadline_at) <= utcnow():
            return False
        message = self.db.get(Message, run.assistant_message_id, populate_existing=True)
        if not message or message.generation_id != run.generation_id:
            return False
        agent = self.db.scalar(select(AgentRun).where(AgentRun.idempotency_key == ChatToolPersistence.key(run.id)))
        try:
            state = json.loads(agent.input_json) if agent else {}
        except (ValueError, TypeError):
            return False
        if not isinstance(state, dict):
            return False
        return (agent.chat_run_id == run.id if agent else False) and state.get("runtime_engine") == VERSION and state.get("generation_id") == run.generation_id

    def _requeue(self, run, reason):
        run.status, run.phase, run.error_code = "queued", "recovering", None
        run.lease_owner, run.lease_expires_at = None, None
        run.queued_expires_at = min(aware(run.deadline_at), utcnow() + timedelta(seconds=settings.chat_run_queue_seconds))
        self._event(run, {"type": "run_status", "status": "queued", "run_id": run.id,
            "reason": reason, "recovery_attempt": run.lease_version})

    def interrupt(self, claim, *, content=None, reasoning=None, error_code="worker_interrupted"):
        run = self._locked_claim(claim, allow_cancel=True)
        if not run:
            return False
        if run.status == "cancel_requested":
            self._terminal(run, "cancelled", "user_cancelled")
        elif run.deadline_at and aware(run.deadline_at) <= utcnow():
            self._terminal(run, "expired", "run_total_timeout")
        elif self._can_recover(run):
            if content is not None:
                run.content = content[:settings.chat_run_max_chars]
            if reasoning is not None:
                run.reasoning_content = reasoning[:max(0, settings.chat_run_max_chars - len(run.content))]
            self._message(run, "streaming")
            self._requeue(run, error_code)
        else:
            self._terminal(run, "interrupted", error_code)
        self.db.commit()
        return True

    def prune_events(self):
        cutoff = utcnow() - timedelta(days=settings.chat_run_event_retention_days)
        runs = list(self.db.scalars(select(ChatRun).where(ChatRun.status.in_(TERMINAL),
            ChatRun.finished_at < cutoff, ChatRun.events_pruned_through < ChatRun.event_sequence)
            .with_for_update(skip_locked=True).limit(100)))
        for run in runs:
            self.db.execute(delete(ChatRunEvent).where(ChatRunEvent.run_id == run.id))
            run.events_pruned_through = run.event_sequence
        self.db.commit()
        return len(runs)
