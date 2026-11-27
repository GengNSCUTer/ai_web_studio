from __future__ import annotations

import socket
import asyncio
import hashlib
import json
import logging
import re
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from time import monotonic
from typing import Callable
from uuid import uuid4

from sqlalchemy import func, literal, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased, load_only

from app.core.database import SessionLocal
from app.models.user_memory import MemoryExtractionJob, MemoryMaintenanceJob, UserMemory
from app.models.memory_trigger_decision import MemoryTriggerDecision
from app.models.chat_run import ChatRun, ChatRunMemoryTrigger
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user_setting import UserSetting
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.memory_job_repo import MemoryExtractionJobRepository
from app.repositories.memory_repo import UserMemoryRepository
from app.repositories.message_repo import MessageRepository
from app.repositories.setting_repo import UserSettingRepository
from app.schemas.memory import MemorySuggestion
from app.services.chat_provider_service import ChatProviderService, resolve_provider_base_url
from app.services.memory_service import MemoryService
from app.services.memory_policy import contains_credential, equivalent_content, memory_identity, normalize
from app.services.memory_extraction_policy import (
    REDACTED, allows_automatic, bounded_source_text, extraction_prompt,
    redact_source, source_digest, source_fragments, source_snapshot, verify_evidence,
)
from app.services.setting_service import SettingService
from app.services.memory_conflict_service import MemoryConflictService
from app.services.memory_trigger_policy import POLICY_VERSION, classify_memory_signal, forget_target, read_trigger_state
from app.services.memory_semantic_gate import semantic_prompt, MIN_BUFFER_MESSAGES, COOLDOWN_SECONDS
from app.services.memory_source_service import source_bound, source_message_ids
from app.services.runtime_lease import aware
from app.services.tokenizer_service import TokenizerEstimator

logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _draft_key(item):
    return item.memory_type, normalize(item.content).casefold()


def _extraction_batch(fragments, cursor, *, existing_text, model, context_window):
    estimator = TokenizerEstimator(model_name=model)
    budget = min(8192, int(context_window or 128000))
    output_budget = min(1600, budget // 4)
    lines = []
    parts: dict[str, list[str]] = {}
    prompt = None
    chars = 0
    for index in range(cursor, len(fragments)):
        message_id, fragment = fragments[index]
        line = f"user [message_id={message_id}]: {fragment}"
        total = chars + len(line) + 1
        if total > 12000:
            break
        trial = extraction_prompt(recent_text="\n".join([*lines, line]), existing_text=existing_text)
        if estimator.estimate_messages_tokens(trial, image_equiv_tokens=0) + output_budget + 64 > budget:
            break
        prompt = trial
        chars = total
        lines.append(line)
        parts.setdefault(message_id, []).append(fragment)
    if not lines:
        raise ValueError("extraction_input_budget_exceeded")
    return prompt, parts, cursor + len(lines), output_budget, "\n".join(lines)


class MemoryExtractionJobService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def reextract(self, user_id: str, memory: UserMemory) -> MemoryExtractionJob:
        """只重新提取同一项目仍存在的用户原话，避免会话迁移引起隐式跨项目复制。"""
        conversation = self.db.scalar(select(Conversation).where(Conversation.id == memory.source_conversation_id,
            Conversation.user_id == user_id).execution_options(populate_existing=True).with_for_update())
        if not conversation or conversation.project_id != memory.project_id or not source_bound(memory):
            raise ValueError("原消息已删除或移出当前范围，请在当前项目提供新的原话")
        setting = self.db.scalar(select(UserSetting).where(UserSetting.user_id == user_id))
        if not setting or not setting.memory_enabled:
            raise ValueError("请先启用长期记忆")
        messages = list(self.db.scalars(select(Message).where(Message.conversation_id == conversation.id,
            Message.id.in_(source_message_ids(memory)), Message.role == "user").order_by(Message.sequence, Message.id)))
        if not messages or len(messages) != len(source_message_ids(memory)):
            raise ValueError("原消息已删除，请在当前项目提供新的原话")
        snapshot = source_snapshot(messages)
        # 同一原文的重复点击复用正在执行或成功的任务；失败后可以手动再提交。
        previous = self.db.scalar(select(MemoryExtractionJob).where(
            MemoryExtractionJob.user_id == user_id, MemoryExtractionJob.conversation_id == conversation.id,
            MemoryExtractionJob.project_id == memory.project_id, MemoryExtractionJob.source_snapshot == snapshot,
            MemoryExtractionJob.trigger_reason == "source_reextract",
            MemoryExtractionJob.error_code.is_(None),
            MemoryExtractionJob.status.in_(["pending", "running", "succeeded"])).limit(1))
        if previous:
            self.db.commit()
            return previous
        job = MemoryExtractionJob(user_id=user_id, conversation_id=conversation.id, project_id=memory.project_id,
            idempotency_key=f"memory-reextract:{memory.id}:{uuid4()}",
            source_message_ids=",".join(item.id for item in messages), source_snapshot=snapshot,
            cursor_end=0, trigger_reason="source_reextract", priority=100)
        self.db.add(job)
        self.db.commit()
        return job

    def enqueue_after_turn(
        self,
        *,
        user_id: str,
        conversation_id: str,
        assistant_message_id: str,
        force: bool = False,
        commit: bool = True,
        trigger: ChatRunMemoryTrigger | None = None,
    ) -> MemoryExtractionJob | None:
        conversation = self.db.scalar(select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user_id
        ).execution_options(populate_existing=True).with_for_update())
        if not conversation:
            return None
        # 重新读取同意状态，不能复用当前Session中早先缓存的开关值。
        setting = self.db.scalar(select(UserSetting).where(UserSetting.user_id == user_id)
            .execution_options(populate_existing=True))
        automatic = bool(setting and setting.memory_auto_candidate_enabled
                         and (trigger is None or trigger.automatic_enabled))

        assistant = self.db.scalar(select(Message).where(Message.conversation_id == conversation_id,
            Message.id == assistant_message_id, Message.role == "assistant", Message.status == "done")
            .options(load_only(Message.id, Message.role, Message.status, Message.sequence)))
        if not assistant:
            if commit:
                self.db.commit()
            return None
        if (setting and not setting.memory_enabled) or (trigger and not trigger.memory_enabled):
            # 总开关关闭后不读正文、不分类或新增触发观测，仅推进位置以避免重新开启时补记。
            if (assistant.sequence or 0) > (conversation.memory_scan_cursor or 0):
                conversation.memory_scan_cursor = assistant.sequence
                conversation.memory_trigger_state = json.dumps({"project_id": conversation.project_id, "pending": []})
            if commit:
                self.db.commit()
            return None
        # 只有旧数据存在空序号时才修复，读取列而非全部历史正文；会话锁仍覆盖此短事务。
        if MessageRepository(self.db).fill_missing_sequences(conversation):
            self.db.refresh(assistant)
        repo = MemoryExtractionJobRepository(self.db)
        outstanding = self.db.scalar(select(MemoryExtractionJob).where(
            MemoryExtractionJob.conversation_id == conversation_id,
            MemoryExtractionJob.status.in_(["pending", "running"])
        ).order_by(MemoryExtractionJob.created_at).limit(1))
        state = read_trigger_state(conversation.memory_trigger_state)
        if state and state.get("project_id") != conversation.project_id:
            # 移动项目不能把旧范围缓冲静默写进新项目；只接收移动后本次的用户输入。
            state = {}
            conversation.memory_scan_cursor = max(0, assistant.sequence - 2)
        deferred_buffer = bool(state.get("semantic_deferred"))
        pending_ids = [item.get("id") for item in state.get("pending", []) if isinstance(item, dict)][:50]
        pending = list(self.db.scalars(select(Message).where(Message.id.in_(pending_ids),
            Message.conversation_id == conversation_id, Message.role == "user", Message.sequence < assistant.sequence)
            .order_by(Message.sequence))) if pending_ids else []
        cursor = max(conversation.memory_scan_cursor or 0, conversation.memory_extraction_cursor or 0)
        new = list(self.db.scalars(select(Message).where(Message.conversation_id == conversation_id,
            Message.role == "user", Message.sequence > (conversation.memory_extraction_cursor or 0 if force else cursor),
            Message.sequence < assistant.sequence, func.trim(Message.content) != "")
            .order_by(Message.sequence).limit(50 - len(pending)))) if len(pending) < 50 else []
        digests = {item.id: source_digest(item) for item in [*pending, *new]}
        failed_sources = self._failed_sources(conversation, digests) if not force and digests else set()
        signals = []
        decisions = []
        seen = set()
        # 一轮只查询一次有限历史与有效记忆数量，避免每个来源再查询数据库。
        recent = list(self.db.scalars(select(Message).where(Message.conversation_id == conversation.id,
            Message.role == "user", Message.sequence < assistant.sequence).order_by(
                Message.sequence.desc()).limit(6)))
        active_ids = select(UserMemory.id).where(*UserMemoryRepository(self.db)._retrieval_filter(
            user_id, conversation.project_id)).limit(100).subquery()
        active_count = self.db.scalar(select(func.count()).select_from(active_ids)) or 0
        for item in [*pending, *new]:
            if item.id in seen or item.id in failed_sources:
                continue
            seen.add(item.id)
            context = self._trigger_context(conversation=conversation, source=item,
                recent=recent, active_count=active_count)
            decision = classify_memory_signal(item.content)
            decisions.append((item, decision, context))
            if (force or decision.action != "skip") and (force or automatic or decision.reason in {"explicit_remember", "explicit_forget"}):
                signals.append((item, decision))
        if new:
            conversation.memory_scan_cursor = max(cursor, max(item.sequence for item in new))
        signals.sort(key=lambda pair: pair[0].sequence)
        # 只存引用与既有来源摘要；每次选取时重新分类，编辑/删除不会继续沿用旧判断。
        # 旧任务没有独立等待快照，用上一次持久化的待处理版本作为起点。
        waiting = state.get("semantic_waiting")
        if not isinstance(waiting, dict):
            waiting = {item["id"]: item.get("digest") for item in state.get("pending", [])
                       if isinstance(item, dict) and "id" in item}
        state = {"project_id": conversation.project_id, "semantic_deferred": deferred_buffer,
            "semantic_waiting": waiting, "pending": [{"id": item.id, "digest": digests[item.id]} for item, _ in signals]}
        conversation.memory_trigger_state = json.dumps(state)
        decision_rows = self._record_decisions(user_id=user_id, conversation=conversation,
            decisions=decisions, observed_turn_id=assistant.id)
        allowed_ids = {item.id for item, _ in signals}
        for row in decision_rows:
            if row.action != "skip" and row.message_id not in allowed_ids:
                row.outcome = "automatic_disabled"
        if outstanding:
            linked = set(outstanding.source_message_ids.split(","))
            for row in decision_rows:
                if row.message_id in linked:
                    row.outcome = "existing_job"
                    row.job_id = outstanding.id
                elif row.message_id in allowed_ids:
                    row.outcome = "deferred_existing_job"
            if commit:
                self.db.commit()
            return outstanding
        interval = max(1, min(int(getattr(setting, "memory_auto_candidate_turn_interval", 4) or 4), 50))
        urgent = max((decision for _, decision in signals), key=lambda decision: decision.priority, default=None)
        if not signals or (not force and not urgent.priority and len(signals) < interval):
            if commit:
                self.db.commit()
            return None
        action = urgent.action if urgent.priority else "buffer"
        reason = "manual_request" if force else urgent.reason if urgent.priority else "batch_fallback"
        if (deferred_buffer and not force and len(signals) < 50
                and all(waiting.get(item.id) == digests[item.id] for item, _ in signals)):
            if commit:
                self.db.commit()
            return None
        # 撤销独立处理；不得将控制语句和其他事实一起交给提取模型。
        source = [item for item, decision in signals if decision.action == "revoke"][:1] if action == "revoke" else [
            item for item, decision in sorted(signals, key=lambda pair: (-pair[1].priority, pair[0].sequence))
            if decision.action != "revoke"]
        if action == "buffer" and (getattr(setting, "memory_semantic_trigger_enabled", False) or deferred_buffer):
            # 高置信度 buffer 保留旧来源；新输入必须进入下一批，避免旧满批次反复挡住新上下文。
            source.sort(key=lambda item: item.sequence, reverse=True)
        # 批次只限制来源条数，正文容量由 Worker 的可恢复分片控制。
        source = sorted(source[:24], key=lambda item: (item.sequence, item.id))
        snapshot = source_snapshot(source)
        identity = json.dumps([conversation.project_id, action, force, snapshot], ensure_ascii=True)
        key = f"memory-extract:{conversation_id}:v2:{hashlib.sha256(identity.encode()).hexdigest()}"
        existing = repo.get_by_idempotency_key(key)
        if existing is None:
            legacy = repo.get_by_idempotency_key(f"memory-extract:{conversation_id}:{source[-1].id}")
            if (legacy and legacy.source_snapshot == snapshot and legacy.project_id == conversation.project_id
                    and legacy.trigger_action == action and (not force or legacy.status == "failed")):
                existing = legacy
        if existing is None and force:
            existing = self.db.scalar(select(MemoryExtractionJob).where(
                MemoryExtractionJob.conversation_id == conversation_id, MemoryExtractionJob.project_id == conversation.project_id,
                MemoryExtractionJob.status == "failed", MemoryExtractionJob.source_snapshot == snapshot,
                MemoryExtractionJob.trigger_action == action, MemoryExtractionJob.trigger_reason != "source_reextract"
            ).order_by(MemoryExtractionJob.created_at.desc()).limit(1))
        if existing:
            if force and existing.status == "failed":
                # 仅显式重新提取才能重试终态失败；刷新证据快照，不重复创建 Job。
                existing.source_snapshot = snapshot
                existing.fragment_cursor, existing.fragment_source_ids, existing.staged_candidates = 0, None, None
                existing.source_message_ids = ",".join(item.id for item in source)
                existing.cursor_end = source[-1].sequence
                existing.trigger_action = action
                existing.trigger_reason = reason
                existing.priority = urgent.priority
                if action != "revoke":
                    existing.control_result = None
                existing.status = "pending"
                existing.attempts = 0
                existing.available_at = utcnow()
                existing.finished_at = None
                existing.error_code = None
                existing.error_message = None
                existing.semantic_action = existing.semantic_confidence = None
                existing.semantic_model_called = False
            for row in decision_rows:
                if row.message_id in set(existing.source_message_ids.split(",")):
                    row.job_id = existing.id
                    row.outcome = "existing_job"
            if commit:
                self.db.commit()
            return existing
        state["semantic_waiting"] = {item.id: digests[item.id] for item, _ in signals} if action == "buffer" else waiting
        conversation.memory_trigger_state = json.dumps(state)
        job = MemoryExtractionJob(
            user_id=user_id,
            conversation_id=conversation_id,
            project_id=getattr(conversation, "project_id", None),
            idempotency_key=key,
            source_message_ids=",".join(str(getattr(message, "id", "")) for message in source),
            source_snapshot=snapshot,
            cursor_end=source[-1].sequence,
            trigger_action=action,
            trigger_reason=reason,
            priority=urgent.priority,
            status="pending",
            available_at=utcnow(),
        )
        if action == "revoke":
            target = forget_target(source[0].content)
            matches = list(self.db.scalars(select(UserMemory).where(UserMemory.user_id == user_id,
                UserMemory.project_id == conversation.project_id, UserMemory.status.in_(["active", "pending"]),
                UserMemory.content == target).limit(2)))
            job.control_result = json.dumps({"targets": [{"id": row.id, "version": row.version} for row in matches]})
        self.db.add(job)
        self.db.flush()
        for row in decision_rows:
            if row.message_id in {item.id for item in source}:
                row.job_id = job.id
                row.job_created = True
                row.outcome = "queued"
        if not commit:
            return job
        try:
            self.db.commit()
            self.db.refresh(job)
            return job
        except IntegrityError:
            self.db.rollback()
            return repo.get_by_idempotency_key(key)

    def _failed_sources(self, conversation, digests):
        # 只隔离当前范围内失败的原文版本，编辑后的新输入仍可排队。
        fields = literal(",") + MemoryExtractionJob.source_message_ids + literal(",")
        snapshots = self.db.scalars(select(MemoryExtractionJob.source_snapshot).where(
            MemoryExtractionJob.conversation_id == conversation.id,
            MemoryExtractionJob.project_id == conversation.project_id,
            MemoryExtractionJob.status == "failed", MemoryExtractionJob.trigger_reason != "source_reextract",
            or_(MemoryExtractionJob.error_code.is_(None), MemoryExtractionJob.error_code.not_in(
                ("source_changed", "conversation_scope_changed", "memory_disabled", "automatic_extraction_disabled"))),
            MemoryExtractionJob.source_snapshot.is_not(None),
            or_(*(fields.contains(f",{item_id},", autoescape=True) for item_id in digests))))
        return {item_id for row in snapshots for item_id, digest in json.loads(row).items()
                if digests.get(item_id) == digest}

    @staticmethod
    def _trigger_context(*, conversation: Conversation, source: object, recent: list,
                         active_count: int) -> dict:
        """构造不含正文的上下文特征，供审计和离线分析使用。"""
        source_sequence = getattr(source, "sequence", 0) or 0
        recent_messages = [item for item in recent if item.sequence < source_sequence][:3]
        return {
            "project_scoped": conversation.project_id is not None,
            "recent_user_turns": len(recent_messages),
            "active_memory_count": int(active_count),
            "message_chars": min(len(getattr(source, "content", "") or ""), 2000),
            "has_question_mark": bool(re.search(r"[?？]", getattr(source, "content", "") or "")),
            "has_existing_memory_scope": bool(active_count),
            "recent_decision_signal": any(
                classify_memory_signal(item.content).action in {"extract_now", "revise"}
                for item in recent_messages
            ),
        }

    def _record_decisions(self, *, user_id: str, conversation: Conversation,
                          decisions: list[tuple[object, object, dict]], observed_turn_id: str) -> list[MemoryTriggerDecision]:
        rows: list[MemoryTriggerDecision] = []
        existing = {(row.message_id, row.source_digest): row for row in self.db.scalars(select(MemoryTriggerDecision).where(
            MemoryTriggerDecision.conversation_id == conversation.id,
            MemoryTriggerDecision.observed_turn_id == observed_turn_id,
            MemoryTriggerDecision.policy_version == POLICY_VERSION))}
        for message, decision, context in decisions:
            digest = source_digest(message)
            exists = existing.get((message.id, digest))
            if exists:
                rows.append(exists)
                continue
            row = MemoryTriggerDecision(user_id=user_id, conversation_id=conversation.id,
                project_id=conversation.project_id, message_id=message.id, source_digest=digest,
                observed_turn_id=observed_turn_id,
                policy_version=POLICY_VERSION, action=decision.action, reason=decision.reason,
                outcome="skipped" if decision.action == "skip" else "buffered",
                priority=decision.priority, context_json=json.dumps(context, ensure_ascii=False))
            self.db.add(row)
            rows.append(row)
        self.db.flush()
        return rows


class MemoryCandidateWorker:
    """独立持久化 Worker；模型网络调用期间不持有认领事务。"""

    HEARTBEAT_SECONDS = 20
    LEASE_SECONDS = 90
    EXTRACTION_QUOTA = 4

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
        owner: str | None = None,
        provider_service: ChatProviderService | None = None,
        conflict_service: MemoryConflictService | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.owner = owner or f"memory-worker-{socket.gethostname()}-{id(self)}"
        self.provider = provider_service or ChatProviderService()
        self.conflicts = conflict_service or MemoryConflictService(session_factory=session_factory, provider_service=self.provider)
        self._extraction_streak = 0
        self._last_queue_sample = monotonic()

    async def run_once(self) -> bool:
        scanned = self._consume_turn_trigger()
        claimed = self._claim()
        if claimed:
            job_id, lease_version = claimed
            from app.services.agent_graph.memory_extraction_graph import run_memory_extraction_graph
            await self._with_heartbeat(MemoryExtractionJob, job_id, lease_version,
                                       run_memory_extraction_graph(self, job_id, lease_version), self._fail)
            self._extraction_streak += 1
        maintained, indexed = False, False
        if not claimed or self._extraction_streak >= self.EXTRACTION_QUOTA:
            self._extraction_streak = 0
            maintained = await self._run_maintenance()
            indexed = await self._run_index()
        if monotonic() - self._last_queue_sample >= 60:
            self._last_queue_sample = monotonic()
            self._log_queue_depths()
        return bool(scanned or claimed or maintained or indexed)

    async def _run_index(self):
        from app.services.memory_index_service import MemoryIndexWorker

        return await MemoryIndexWorker(session_factory=self.session_factory).run_once()

    def _log_queue_depths(self):
        now = utcnow()
        try:
            with self.session_factory() as db:
                for model, queue in ((MemoryExtractionJob, "extraction"), (MemoryMaintenanceJob, "maintenance")):
                    count, oldest = db.execute(select(func.count(), func.min(model.created_at)).where(
                        model.status.in_(["pending", "running"]))).one()
                    logger.info("memory_queue queue=%s unfinished=%d oldest_wait_seconds=%d", queue, count,
                                max(0, int((now - aware(oldest)).total_seconds())) if oldest else 0)
                from app.services.memory_index_service import MemoryIndexWorker

                if db.get_bind().dialect.name == "postgresql":
                    count, oldest = db.execute(select(func.count(), func.min(UserMemory.updated_at))
                        .join(UserSetting, UserSetting.user_id == UserMemory.user_id)
                        .where(*MemoryIndexWorker.eligible(now),
                               UserSetting.memory_enabled.is_(True))).one()
                    logger.info("memory_queue queue=index ready=%d oldest_update_seconds=%d", count,
                                max(0, int((now - aware(oldest)).total_seconds())) if oldest else 0)
        except Exception as exc:
            logger.warning("memory_queue sampling_failed=%s", type(exc).__name__)

    def _consume_turn_trigger(self) -> bool:
        with self.session_factory() as db:
            # 与编辑、建轮、已有记忆扫描共享会话锁。不同会话可并行消费。
            previous = aliased(ChatRun)
            earlier_pending = select(previous.id).join(ChatRunMemoryTrigger,
                ChatRunMemoryTrigger.run_id == previous.id).where(previous.conversation_id == ChatRun.conversation_id,
                or_(previous.finished_at < ChatRun.finished_at,
                    (previous.finished_at == ChatRun.finished_at) & (previous.id < ChatRun.id)))
            row = db.execute(select(ChatRunMemoryTrigger, ChatRun.user_id, ChatRun.conversation_id,
                ChatRun.assistant_message_id, ChatRun.generation_id, Conversation.project_id)
                .join(ChatRun, ChatRun.id == ChatRunMemoryTrigger.run_id)
                .join(Conversation, Conversation.id == ChatRun.conversation_id)
                .where(ChatRunMemoryTrigger.available_at <= utcnow(), Conversation.user_id == ChatRun.user_id,
                       ~earlier_pending.exists())
                .order_by(ChatRunMemoryTrigger.available_at, ChatRunMemoryTrigger.run_id)
                .with_for_update(of=Conversation, skip_locked=True).limit(1)).first()
            if row is None:
                return False
            candidate, user_id, conversation_id, assistant_message_id, generation_id, project_id = row
            trigger = db.get(ChatRunMemoryTrigger, candidate.run_id, with_for_update=True, populate_existing=True)
            if trigger is None or aware(trigger.available_at) > utcnow():
                return False
            try:
                with db.begin_nested():
                    if trigger.project_id == project_id and db.scalar(select(Message.generation_id).where(
                        Message.id == assistant_message_id, Message.conversation_id == conversation_id
                    )) == generation_id:
                        MemoryExtractionJobService(db).enqueue_after_turn(user_id=user_id,
                            conversation_id=conversation_id, assistant_message_id=assistant_message_id,
                            commit=False, trigger=trigger)
                    db.delete(trigger)
            except Exception as exc:
                # 保留待消费事件，单条失败暂退后让其他会话继续前进。
                logger.warning("Chat长期记忆触发暂缓 run_id=%s error=%s", trigger.run_id, type(exc).__name__)
                trigger.available_at = utcnow() + timedelta(seconds=15)
            db.commit()
            return True

    async def _with_heartbeat(self, model, job_id, lease_version, operation, fail):
        stop, lost = asyncio.Event(), asyncio.Event()
        work = asyncio.create_task(operation)
        heartbeat = asyncio.create_task(self._heartbeat_loop(job_id, lease_version, stop, lost, work, model=model))
        try:
            await work
        except asyncio.CancelledError:
            # 续租失效只退出旧执行者；正常停机仍向上抛取消，交给租约恢复。
            if not lost.is_set():
                raise
        except Exception as exc:
            fail(job_id, lease_version, exc)
        finally:
            stop.set()
            await heartbeat

    async def _heartbeat_loop(self, job_id, lease_version, stop, lost, work, *, model=MemoryExtractionJob):
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), self.HEARTBEAT_SECONDS)
                return
            except asyncio.TimeoutError:
                pass
            try:
                with self.session_factory() as db:
                    job = db.scalar(select(model).where(model.id == job_id).with_for_update())
                    if job and job.lease_version == lease_version and job.status in {"succeeded", "ready", "completed"}:
                        # 保存已完成，不能在积压排队节点前把自己的图取消。
                        return
                    if not self._owns(job, lease_version):
                        lost.set()
                    else:
                        job.lease_expires_at = utcnow() + timedelta(seconds=self.LEASE_SECONDS)
                        db.commit()
            except Exception:
                # 数据库不可确认时不继续保存旧结果，由持久化Job到期后重领。
                lost.set()
            if lost.is_set():
                work.cancel()
                return

    async def _run_maintenance(self):
        from app.services.memory_maintenance_service import MemoryMaintenanceService, generate_proposals

        model = MemoryMaintenanceJob
        now = utcnow()
        with self.session_factory() as db:
            claimable = or_(model.status == "pending", (model.status == "running") & (model.lease_expires_at <= now))
            for job in db.scalars(select(model).where(claimable, model.attempts >= model.max_attempts)
                                   .limit(20).with_for_update(skip_locked=True)):
                job.status, job.error_code, job.finished_at = "failed", "attempts_exhausted", now
                job.lease_owner = job.lease_expires_at = None
                job.version += 1
            db.flush()
            job = db.scalar(select(model).where(claimable, model.attempts < model.max_attempts,
                or_(model.available_at.is_(None), model.available_at <= now))
                .order_by(model.created_at, model.id).limit(1).with_for_update(skip_locked=True))
            if not job:
                db.commit()
                return False
            job.status, job.lease_owner = "running", self.owner
            job.attempts += 1
            job.lease_version += 1
            job.lease_expires_at = now + timedelta(seconds=self.LEASE_SECONDS)
            job_id, lease_version, user_id = job.id, job.lease_version, job.user_id
            db.commit()

        async def execute():
            with self.session_factory() as db:
                job = db.get(model, job_id)
                if not self._owns(job, lease_version):
                    return
                versions, facts, call = MemoryMaintenanceService(db).load(job)
            drafts, metrics = await generate_proposals(self.provider, facts, call)
            with self.session_factory() as db:
                service = MemoryMaintenanceService(db)
                with service.repo.mutation(user_id):
                    job = service.get(user_id, job_id, lock=True)
                    if self._owns(job, lease_version):
                        service.complete(job, versions, drafts, metrics)

        await self._with_heartbeat(model, job_id, lease_version, execute(), self._fail_maintenance)
        return True

    def _fail_maintenance(self, job_id, lease_version, exc):
        with self.session_factory() as db:
            job = db.get(MemoryMaintenanceJob, job_id, with_for_update=True)
            if not self._owns(job, lease_version):
                return
            job.lease_owner = job.lease_expires_at = None
            if not isinstance(exc, (ValueError, LookupError, PermissionError)) and job.attempts < job.max_attempts:
                job.status, job.error_code = "pending", "provider_unavailable"
                job.available_at = utcnow() + timedelta(seconds=2 ** job.attempts)
            else:
                job.status, job.finished_at = "failed", utcnow()
                if isinstance(exc, PermissionError):
                    job.error_code = "memory_disabled"
                elif isinstance(exc, LookupError):
                    job.error_code = "project_unavailable"
                elif isinstance(exc, ValueError):
                    job.error_code = "invalid_model_output"
                else:
                    job.error_code = "provider_unavailable"
            logger.warning("memory_maintenance status=%s reason=%s exception=%s", job.status, job.error_code,
                           type(exc).__name__)
            job.version += 1
            db.commit()

    @staticmethod
    def _decode_candidates(raw, snapshot):
        """模型JSON只产生候选；非法输出不得冒充空数组推进游标。"""
        normalized = raw.strip()
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", normalized, re.S)
        decoded = json.loads(fenced.group(1) if fenced else normalized)
        if not isinstance(decoded, list):
            raise ValueError("invalid_extraction_output")
        if any(not isinstance(item, dict)
               or not all(isinstance(item.get(name), str) and item[name].strip() for name in ("title", "content"))
               or any(item.get(name) is not None and not isinstance(item[name], str)
                      for name in ("memory_type", "confidence", "reason", "source_message_id", "evidence_quote"))
               for item in decoded):
            raise ValueError("invalid_extraction_item")
        return MemoryService.parse_suggestion_json(raw, max_candidates=5,
            source_conversation_id=snapshot["conversation_id"], source_message_ids=snapshot["source_message_ids"])

    def _stage_batch(self, job_id, lease_version, snapshot, suggestions):
        """只保存当前输入片段能支持的草稿。非末片主动释放租约，方便恢复和让出队列。"""
        with self._locked_extraction(job_id, lease_version) as (db, _, job):
            if not job or job.fragment_cursor != snapshot["checkpoint_cursor"]:
                return None
            _, sources, settings = self._locked_sources(db, job)
            if source_snapshot(sources) != snapshot["source_snapshot"]:
                raise ValueError("source_changed")
            selected = self._extraction_sources(job, sources, settings)
            if ",".join(item.id for item in selected) != snapshot["source_message_ids"]:
                job.fragment_cursor, job.fragment_source_ids, job.staged_candidates = 0, None, None
                self._release_batch(job)
                return None
            if not job.source_snapshot:
                job.source_snapshot = snapshot["source_snapshot"]
            previous = self._staged_candidates(job) if snapshot["fragment_cursor"] else []
            drafts = {_draft_key(item): item for item in previous}
            for item in suggestions:
                checked = verify_evidence(item, [], fragments=snapshot["fragments"])
                if (checked is None or REDACTED in f"{checked.title}\n{checked.content}"
                        or redact_source(checked.title) != checked.title or redact_source(checked.content) != checked.content):
                    continue
                key = _draft_key(checked)
                if key not in drafts or (checked.evidence_verified and not drafts[key].evidence_verified):
                    drafts[key] = checked
            job.fragment_cursor = snapshot["next_fragment"]
            job.fragment_source_ids = snapshot["source_message_ids"]
            job.staged_candidates = [item.model_dump(mode="json", exclude_none=True) for item in drafts.values()]
            logger.debug("memory_extraction progress=%d total=%d drafts=%d", job.fragment_cursor,
                         snapshot["fragment_count"], len(drafts))
            if job.fragment_cursor < snapshot["fragment_count"]:
                self._release_batch(job)
                return None
            return list(drafts.values())

    @staticmethod
    def _release_batch(job):
        job.status, job.attempts, job.available_at = "pending", 0, utcnow()
        job.lease_owner = job.lease_expires_at = None
        job.error_code = job.error_message = None

    @staticmethod
    def _staged_candidates(job):
        return [MemorySuggestion.model_validate(item) for item in job.staged_candidates or []]

    @staticmethod
    def _extraction_sources(job, sources, settings):
        if job.trigger_reason == "explicit_remember" and not (settings and settings.memory_auto_candidate_enabled):
            return [item for item in sources if classify_memory_signal(item.content).reason == "explicit_remember"]
        return sources

    def _enqueue_backlog(self, job_id: str) -> None:
        """执行期间新增的消息在下一批处理，不等用户再发一轮。"""
        with self.session_factory() as db:
            job = db.get(MemoryExtractionJob, job_id)
            if not job or job.status != "succeeded" or job.trigger_reason == "source_reextract":
                return
            pending_turn = select(ChatRunMemoryTrigger.run_id).join(ChatRun).where(
                ChatRun.conversation_id == job.conversation_id)
            assistant = db.scalar(select(Message).where(Message.conversation_id == job.conversation_id,
                Message.role == "assistant", Message.status == "done", ~pending_turn.exists()).order_by(
                    *MessageRepository.order_columns(descending=True)).limit(1))
            if assistant:
                MemoryExtractionJobService(db).enqueue_after_turn(user_id=job.user_id,
                    conversation_id=job.conversation_id, assistant_message_id=assistant.id)

    def _claim(self) -> tuple[str, int] | None:
        with self.session_factory() as db:
            job = MemoryExtractionJobRepository(db).claim_next()
            if not job:
                db.commit()
                return None
            now = utcnow()
            job.status = "running"
            job.attempts = (job.attempts or 0) + 1
            job.lease_owner = self.owner
            job.lease_version = (job.lease_version or 0) + 1
            job.lease_expires_at = now + timedelta(seconds=self.LEASE_SECONDS)
            job.started_at = job.started_at or now
            version = job.lease_version
            db.commit()
            return job.id, version

    def _load_snapshot(self, job_id: str, lease_version: int) -> dict | None:
        with self.session_factory() as db:
            job = db.get(MemoryExtractionJob, job_id)
            if not self._owns(job, lease_version):
                return None
            conversation = ConversationRepository(db).get_by_user(job.conversation_id, job.user_id)
            if not conversation:
                raise ValueError("conversation_missing")
            if conversation.project_id != job.project_id:
                raise ValueError("conversation_scope_changed")
            setting_service = SettingService(UserSettingRepository(db))
            settings = setting_service.get_or_create_user_settings(job.user_id)
            self._check_consent(job, settings)
            if job.trigger_action == "revoke":
                return {"control_request": True}
            sources = self._load_sources(db, job)
            messages = self._extraction_sources(job, sources, settings)
            selected = ",".join(item.id for item in messages)
            cursor = job.fragment_cursor if job.fragment_source_ids == selected else 0
            fragments = list(source_fragments(messages))
            if not fragments or cursor > len(fragments):
                raise ValueError("source_messages_missing")
            result = {
                "user_id": job.user_id, "project_id": job.project_id, "conversation_id": job.conversation_id,
                "source_message_ids": selected, "trigger_action": job.trigger_action,
            }
            if cursor == len(fragments):
                return {**result, "staged_candidates": self._staged_candidates(job)}
            existing_text = MemoryService(
                UserMemoryRepository(db), ConversationRepository(db)
            ).build_existing_memory_text(job.user_id, max_chars=1600, project_id=job.project_id)
            try:
                prompt, parts, next_fragment, max_tokens, recent_text = _extraction_batch(
                    fragments, cursor, existing_text=existing_text, model=settings.default_model,
                    context_window=settings.model_context_window)
            except ValueError:
                prompt, parts, next_fragment, max_tokens, recent_text = _extraction_batch(
                    fragments, cursor, existing_text="", model=settings.default_model,
                    context_window=settings.model_context_window)
            provider_call = {
                "provider_type": settings.provider_type,
                "base_url": resolve_provider_base_url(provider_type=settings.provider_type,
                    configured_ollama_base_url=settings.ollama_base_url,
                    configured_api_base_url=settings.api_base_url),
                "api_key": setting_service.resolve_provider_api_key(job.user_id),
                "model_name": settings.default_model,
                "messages": prompt,
                "temperature": 0.1, "top_p": 0.8, "max_tokens": max_tokens,
            }
            whole_input = (not cursor and next_fragment == len(fragments)
                           and len(fragments) == len(messages))
            eligible = bool(whole_input and job.trigger_action == "buffer" and not job.semantic_model_called
                and settings.memory_semantic_trigger_enabled
                and len(messages) >= MIN_BUFFER_MESSAGES)
            buffer_full = len(read_trigger_state(conversation.memory_trigger_state).get("pending", [])) >= 50
            result.update({
                "provider_call": provider_call,
                "fragment_cursor": cursor, "checkpoint_cursor": job.fragment_cursor, "next_fragment": next_fragment,
                "fragment_count": len(fragments), "fragments": parts, "source_snapshot": source_snapshot(sources),
                "semantic_eligible": eligible,
                "semantic_resume": job.semantic_action if whole_input and job.semantic_confidence == "high"
                    and job.semantic_action in {"skip", "buffer"}
                    and not (job.semantic_action == "buffer" and buffer_full) else None,
                "semantic_buffer_full": buffer_full,
            })
            if eligible:
                recent = list(db.scalars(select(Message).where(Message.conversation_id == job.conversation_id,
                    Message.role == "user", Message.sequence <= job.cursor_end).order_by(
                        Message.sequence.desc()).limit(4)))
                result["semantic_call"] = {**provider_call, "temperature": 0.0, "max_tokens": 240,
                    "messages": semantic_prompt(source_text=recent_text,
                        recent_context=bounded_source_text(list(reversed(recent)), max_chars=4000)[0],
                        existing_text=redact_source(existing_text)[:2000], project_scoped=job.project_id is not None)}
            return result

    def _complete(self, job_id: str, lease_version: int, suggestions: list) -> bool:
        with self._locked_extraction(job_id, lease_version) as (db, repo, job):
            if job:
                self._persist_results(db, repo, job, suggestions)
            return job is not None

    @contextmanager
    def _locked_extraction(self, job_id, lease_version):
        with self.session_factory() as db:
            user_id = db.scalar(select(MemoryExtractionJob.user_id).where(MemoryExtractionJob.id == job_id))
            repo = UserMemoryRepository(db)
            if not user_id:
                yield db, repo, None
                return
            with repo.mutation(user_id):
                job = db.get(MemoryExtractionJob, job_id, with_for_update=True, populate_existing=True)
                yield db, repo, job if self._owns(job, lease_version) else None

    def _load_sources(self, db: Session, job: MemoryExtractionJob) -> list:
        ids = set(job.source_message_ids.split(","))
        messages = [item for item in MessageRepository(db).list_by_ids_and_conversation(job.conversation_id, list(ids))
                    if item.id in ids and item.role == "user"]
        if job.source_snapshot:
            snapshot = json.loads(job.source_snapshot)
            if set(snapshot) != {item.id for item in messages} or any(
                source_digest(item) != snapshot[item.id] for item in messages
            ):
                raise ValueError("source_changed")
        return messages

    def _locked_sources(self, db, job):
        conversation = db.scalar(select(Conversation).where(Conversation.id == job.conversation_id,
            Conversation.user_id == job.user_id).execution_options(populate_existing=True).with_for_update())
        if not conversation or conversation.project_id != job.project_id:
            raise ValueError("conversation_scope_changed")
        messages = self._load_sources(db, job)
        settings = db.scalar(select(UserSetting).where(UserSetting.user_id == job.user_id).with_for_update())
        self._check_consent(job, settings)
        return conversation, messages, settings

    def _persist_results(self, db: Session, repo: UserMemoryRepository, job: MemoryExtractionJob, suggestions: list) -> None:
        # 与手动撤销/更正使用同一用户锁，防止旧 Worker 覆盖用户刚完成的操作。
        conversation, sources, settings = self._locked_sources(db, job)
        messages = self._extraction_sources(job, sources, settings)
        if job.fragment_source_ids:
            selected = set(job.fragment_source_ids.split(","))
            messages = [item for item in messages if item.id in selected]
        service = MemoryService(repo, ConversationRepository(db))
        normalized_sources = {item.id: [normalize(redact_source(item.content))] for item in messages} if suggestions else {}
        verified = [verify_evidence(item, [], fragments=normalized_sources) for item in suggestions]
        checked = [item.model_copy(update={"project_id": job.project_id}) for item in verified if item]
        if settings and not settings.memory_auto_candidate_enabled and job.trigger_reason == "explicit_remember":
            explicit_ids = {item.id for item in messages if classify_memory_signal(item.content).reason == "explicit_remember"}
            checked = [item for item in checked if item.source_message_id in explicit_ids]
        if not checked:
            self._finish_job(conversation, job, 0, consumed={item.id for item in messages})
            return
        exact_only = all((item.conflict_review or {}).get("version_scope") == "fact_key" for item in checked)
        keys = {item.conflict_review["fact_key"] for item in checked} if exact_only else None
        hashes = {service.memory_content_hash(user_id=job.user_id, memory_type=item.memory_type,
            content=item.content, project_id=job.project_id) for item in checked}
        scoped = service.valid_sources(repo.list_for_conflict(job.user_id, job.project_id,
            fact_keys=keys, content_hashes=hashes, statuses=None))
        # 历史撤销还会按来源或同文匹配，须在限定范围内保留原判断。
        closed = repo.list_for_conflict(job.user_id, job.project_id, statuses=("revoked", "rejected", "superseded"))
        legacy_enriched = service.enrich_suggestion_risks(
            suggestions=checked,
            existing_memories=scoped,
            scoped=True,
        )
        enriched = [original if original.conflict_review else legacy for original, legacy in zip(checked, legacy_enriched)]
        job_sources = set(job.source_message_ids.split(","))
        created = 0
        for suggestion in enriched:
            if contains_credential(suggestion.title, suggestion.content) or REDACTED in suggestion.content:
                continue
            content_hash = service.memory_content_hash(
                user_id=job.user_id,
                memory_type=suggestion.memory_type,
                content=suggestion.content,
                project_id=job.project_id,
            )
            if any(row.content_hash == content_hash for row in scoped):
                continue
            source = next((item for item in messages if item.id == suggestion.source_message_id), None)
            automatic = bool(job.trigger_reason != "source_reextract" and job.source_snapshot and settings and settings.memory_enabled
                and settings.memory_auto_candidate_enabled and settings.memory_auto_activate_enabled
                and allows_automatic(suggestion, source)
                and not (suggestion.conflict_review or {}).get('requires_review', False))
            memory = UserMemory(
                user_id=job.user_id,
                memory_type=suggestion.memory_type,
                title=suggestion.title,
                content=suggestion.content,
                source="auto_confirmed" if automatic else "auto_candidate",
                source_conversation_id=job.conversation_id,
                source_message_ids=suggestion.source_message_ids,
                evidence_quote=suggestion.evidence_quote if suggestion.evidence_verified else None,
                extraction_job_id=job.id,
                confidence=suggestion.confidence,
                is_enabled=automatic,
                status="active" if automatic else "pending",
                project_id=job.project_id,
                importance={"high": 0.9, "medium": 0.6, "low": 0.3}.get(suggestion.confidence or "", 0.5),
                sensitivity="sensitive" if suggestion.risk_level == "sensitive" else "normal",
                risk_level=suggestion.risk_level,
                candidate_reason=suggestion.risk_reason or suggestion.reason,
                conflict_review_json=json.dumps(suggestion.conflict_review, ensure_ascii=False) if suggestion.conflict_review else None,
                content_hash=content_hash,
                supersedes_memory_id=suggestion.conflict_memory_id,
            )
            service._set_identity(memory)
            blocked = any((item.fact_key or memory_identity(item.memory_type, item.title, item.content).key) == memory.fact_key
                or equivalent_content(item.content, memory.content) or bool(source_message_ids(item) & job_sources)
                for item in closed)
            if blocked or not suggestion.evidence_verified:
                memory.is_enabled = False
                memory.status = "pending"
                memory.source = "auto_candidate"
                if memory.risk_level == "safe":
                    memory.risk_level = "review_required"
                memory.candidate_reason = "用户已有撤销/替换历史，需重新确认" if blocked else "缺少可核验的逐条用户原文，需人工审核"
            could_auto_activate = (job.trigger_reason != "source_reextract" and not blocked and settings
                and settings.memory_auto_candidate_enabled and settings.memory_auto_activate_enabled and job.source_snapshot)
            if (could_auto_activate and service._candidate_content_risk(suggestion)[0] == "safe"
                    and allows_automatic(suggestion.model_copy(update={"risk_level": "safe"}), source)):
                self._hold_competing_automatic(memory, scoped, repo)
            if memory.status == "active":
                # 同批次内也重新检查最新事实，禁止两条候选同时自动写入矛盾值。
                try:
                    service._validate_persistence(memory)
                    service._validate_activation(memory, None)
                except ValueError:
                    memory.is_enabled = False
                    memory.status = "pending"
                    memory.source = "auto_candidate"
                    memory.risk_level = "review_required"
                    memory.candidate_reason = "保存时记忆版本变化或出现冲突，需重新检查后确认"
                    if suggestion.conflict_review:
                        memory.conflict_review_json = json.dumps({**suggestion.conflict_review,
                            'status': 'stale', 'requires_review': True}, ensure_ascii=False)
                else:
                    memory.review_at = utcnow()
            repo.flush(memory)
            scoped.append(memory)
            created += 1
        self._finish_job(conversation, job, created, consumed={item.id for item in messages})

    @staticmethod
    def _hold_competing_automatic(memory, scoped, repo):
        """两个独立来源竞争同一事实时，不能由 Worker 提交顺序决定当前值。"""
        identity = memory_identity(memory.memory_type, memory.title, memory.content)
        competitors = []
        for row in scoped:
            if row.source_conversation_id == memory.source_conversation_id or not row.evidence_quote:
                continue
            old = memory_identity(row.memory_type, row.title, row.content)
            if old.key != identity.key or old.value == identity.value or equivalent_content(row.content, memory.content):
                continue
            if row.status == "active" and row.source == "auto_confirmed" and row.is_enabled:
                competitors.append(row)
            elif row.status == "pending" and row.needs_explicit_choice:
                competitors.append(row)
            elif row.status == "active" and row.is_enabled and row.source != "auto_confirmed":
                return
        if not competitors:
            return
        for row in [*competitors, memory]:
            row.status, row.is_enabled, row.source = "pending", False, "auto_candidate"
            row.risk_level = "review_required"
            row.needs_explicit_choice = True
            row.candidate_reason = "不同会话提供了相冲突的事实，请选择当前版本"
            row.supersedes_memory_id = row.conflict_review_json = None
            if row is not memory:
                row.version += 1
                repo.flush(row)

    @staticmethod
    def _check_consent(job, settings):
        if settings and not settings.memory_enabled:
            raise PermissionError("memory_disabled")
        if (job.trigger_reason not in {"explicit_remember", "explicit_forget", "manual_request", "legacy_batch", "source_reextract"}
                and not (settings and settings.memory_auto_candidate_enabled)):
            raise PermissionError("automatic_extraction_disabled")

    @staticmethod
    def _finish_job(conversation, job, count, *, consumed=None):
        """任务、结果和缓冲消费同事务；失败或旧租约不能吃掉来源。"""
        job.status = "succeeded"
        job.result_count = count
        job.finished_at = utcnow()
        job.lease_owner = None
        job.lease_expires_at = None
        job.error_code = None
        job.error_message = None
        job.fragment_source_ids = job.staged_candidates = None
        if job.trigger_reason == "source_reextract":
            return
        conversation.memory_extraction_cursor = max(conversation.memory_extraction_cursor or 0, job.cursor_end or 0)
        state = read_trigger_state(conversation.memory_trigger_state)
        state["project_id"] = conversation.project_id
        if consumed is None:
            consumed = set(job.source_message_ids.split(","))
        state["pending"] = [item for item in state.get("pending", []) if item.get("id") not in consumed]
        if not state["pending"]:
            state["semantic_deferred"] = False
            state.pop("semantic_waiting", None)
        conversation.memory_trigger_state = json.dumps(state)

    def _reserve_semantic_call(self, job_id: str, lease_version: int) -> bool:
        """网络前提交用户级调用间隔与 Job 标记；崩溃重领不再次增加价值判断调用。"""
        with self._locked_extraction(job_id, lease_version) as (db, _, job):
            if not job or job.trigger_action != "buffer" or job.semantic_model_called:
                return False
            conversation = db.scalar(select(Conversation).where(Conversation.id == job.conversation_id,
                Conversation.user_id == job.user_id).with_for_update())
            if not conversation or conversation.project_id != job.project_id:
                raise ValueError("conversation_scope_changed")
            sources = self._load_sources(db, job)
            settings = db.scalar(select(UserSetting).where(UserSetting.user_id == job.user_id).with_for_update())
            self._check_consent(job, settings)
            if not settings.memory_semantic_trigger_enabled or len(sources) < MIN_BUFFER_MESSAGES:
                return False
            last = settings.memory_semantic_last_checked_at
            if last and last.replace(tzinfo=last.tzinfo or timezone.utc) > utcnow() - timedelta(seconds=COOLDOWN_SECONDS):
                return False
            settings.memory_semantic_last_checked_at = utcnow()
            job.semantic_model_called = True
            db.execute(update(MemoryTriggerDecision).where(MemoryTriggerDecision.job_id == job.id)
                .values(semantic_model_called=True))
            return True

    def _record_semantic_decision(self, job_id: str, lease_version: int, action: str,
                                  confidence: str) -> bool:
        """结果只记录动作与置信度；不持久化模型自由文本，以免复制隐私。"""
        with self.session_factory() as db:
            job = db.scalar(select(MemoryExtractionJob).where(MemoryExtractionJob.id == job_id).with_for_update())
            if not self._owns(job, lease_version):
                return False
            self._load_sources(db, job)
            job.semantic_action, job.semantic_confidence = action, confidence
            db.execute(update(MemoryTriggerDecision).where(MemoryTriggerDecision.job_id == job.id)
                .values(semantic_action=action, semantic_confidence=confidence))
            db.commit()
            return True

    def _complete_semantic_skip(self, job_id: str, lease_version: int) -> bool:
        # 复用原文/范围/撤销/开关/租约检查，而不是只把 Job 标成成功。
        return self._complete(job_id, lease_version, [])

    def _defer_semantic_buffer(self, job_id: str, lease_version: int) -> bool:
        """等待后续上下文，不消费来源；同一批次幂等键不再排队直到有新输入。"""
        with self._locked_extraction(job_id, lease_version) as (db, _, job):
            if not job:
                return False
            conversation = db.scalar(select(Conversation).where(Conversation.id == job.conversation_id,
                Conversation.user_id == job.user_id).with_for_update())
            if not conversation or conversation.project_id != job.project_id:
                raise ValueError("conversation_scope_changed")
            self._load_sources(db, job)
            settings = db.scalar(select(UserSetting).where(UserSetting.user_id == job.user_id).with_for_update())
            self._check_consent(job, settings)
            job.status, job.result_count, job.finished_at = "succeeded", 0, utcnow()
            job.lease_owner, job.lease_expires_at = None, None
            state = read_trigger_state(conversation.memory_trigger_state)
            state["semantic_deferred"] = True
            if not state.get("semantic_waiting"):
                state["semantic_waiting"] = {item["id"]: item["digest"] for item in state.get("pending", [])}
            conversation.memory_trigger_state = json.dumps(state)
            return True

    def _complete_control(self, job_id, lease_version):
        """撤销仅接受原始用户明确命令和唯一精确目标；不调用模型猜目标。"""
        with self._locked_extraction(job_id, lease_version) as (db, repo, job):
            if not job:
                return False
            conversation = db.scalar(select(Conversation).where(Conversation.id == job.conversation_id,
                Conversation.user_id == job.user_id).execution_options(populate_existing=True).with_for_update())
            if not conversation or conversation.project_id != job.project_id:
                raise ValueError("conversation_scope_changed")
            sources = self._load_sources(db, job)
            settings = db.scalar(select(UserSetting).where(UserSetting.user_id == job.user_id).with_for_update())
            self._check_consent(job, settings)
            if len(sources) != 1 or classify_memory_signal(sources[0].content).action != "revoke":
                raise ValueError("invalid_control_source")
            targets = json.loads(job.control_result or "{}").get("targets", [])
            result = "needs_review"
            row = None
            if len(targets) == 1:
                target = targets[0]
                row = db.scalar(select(UserMemory).where(UserMemory.id == target["id"],
                    UserMemory.user_id == job.user_id, UserMemory.project_id == job.project_id)
                    .execution_options(populate_existing=True).with_for_update())
                if (row and row.version == target["version"] and row.status in {"active", "pending"}
                        and MemoryService(repo, ConversationRepository(db))._memory_scope(row, job.user_id) == job.project_id
                        and row.content == forget_target(sources[0].content)):
                    row.status, row.is_enabled = "revoked", False
                    row.review_at = utcnow()
                    row.version += 1
                    repo.flush(row)
                    result = "revoked"
            job.control_result = json.dumps({"outcome": result, "memory_id": row.id if result == "revoked" else None})
            self._finish_job(conversation, job, int(result == "revoked"))
            return True

    def _fail(self, job_id: str, lease_version: int, exc: Exception) -> None:
        with self.session_factory() as db:
            job = db.scalars(
                select(MemoryExtractionJob).where(MemoryExtractionJob.id == job_id).with_for_update()
            ).first()
            if not self._owns(job, lease_version):
                return
            retryable = not isinstance(exc, (ValueError, PermissionError))
            if retryable and job.attempts < job.max_attempts:
                job.status = "pending"
                job.available_at = utcnow() + timedelta(seconds=min(60, 2 ** job.attempts))
                job.error_code = "provider_unavailable"
                job.error_message = "模型服务暂时不可用，候选提取任务将有限重试。"
            else:
                job.status = "failed"
                job.fragment_source_ids = job.staged_candidates = None
                job.finished_at = utcnow()
                reason = str(exc) if isinstance(exc, (ValueError, PermissionError)) else ""
                job.error_code = reason if reason in {
                    "source_changed", "conversation_scope_changed", "memory_disabled", "automatic_extraction_disabled",
                    "extraction_input_budget_exceeded", "invalid_extraction_output", "invalid_extraction_item",
                } else "invalid_source" if isinstance(exc, ValueError) else "extraction_failed"
                job.error_message = "长期记忆提取失败，本次未保存候选或推进游标。"
            logger.warning("memory_extraction status=%s reason=%s exception=%s", job.status, job.error_code,
                           type(exc).__name__)
            job.lease_owner = None
            job.lease_expires_at = None
            db.commit()

    def _owns(self, job: MemoryExtractionJob | None, lease_version: int) -> bool:
        return bool(
            job
            and job.status == "running"
            and job.lease_owner == self.owner
            and job.lease_version == lease_version
            and job.lease_expires_at is not None
            and job.lease_expires_at.replace(tzinfo=job.lease_expires_at.tzinfo or timezone.utc) > utcnow()
        )
