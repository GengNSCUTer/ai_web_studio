from __future__ import annotations

import socket
import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, load_only

from app.core.database import SessionLocal
from app.models.user_memory import MemoryExtractionJob, UserMemory
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user_setting import UserSetting
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.memory_job_repo import MemoryExtractionJobRepository
from app.repositories.memory_repo import UserMemoryRepository
from app.repositories.message_repo import MessageRepository
from app.repositories.setting_repo import UserSettingRepository
from app.services.chat_provider_service import ChatProviderService, resolve_provider_base_url
from app.services.memory_service import MemoryService
from app.services.memory_policy import contains_credential, equivalent_content, memory_identity
from app.services.memory_extraction_policy import (
    REDACTED, allows_automatic, extraction_prompt,
    redact_source, source_digest, source_snapshot, verify_evidence,
)
from app.services.setting_service import SettingService
from app.services.memory_conflict_service import MemoryConflictService
from app.services.memory_trigger_policy import classify_memory_signal, forget_target, read_trigger_state


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _source_messages(messages: list[object]) -> list[object]:
    return [
        message
        for message in messages
        if getattr(message, "role", "") == "user"
        and (getattr(message, "content", None) or "").strip()
    ]


def _source_text(messages: list[object], *, max_chars: int = 12000) -> str:
    lines: list[str] = []
    total = 0
    for message in _source_messages(messages):
        content = " ".join(redact_source(getattr(message, "content", "")).split()).strip()[:1200]
        line = f"user [message_id={message.id}]: {content}"
        if total + len(line) > max_chars:
            break
        lines.append(line)
        total += len(line)
    return "\n".join(lines)


def _candidate_prompt(*, recent_text: str, existing_text: str) -> list[dict[str, str]]:
    return extraction_prompt(recent_text=recent_text, existing_text=existing_text)


class MemoryExtractionJobService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def enqueue_after_turn(
        self,
        *,
        user_id: str,
        conversation_id: str,
        assistant_message_id: str,
        force: bool = False,
    ) -> MemoryExtractionJob | None:
        conversation = self.db.scalar(select(Conversation).where(
            Conversation.id == conversation_id, Conversation.user_id == user_id
        ).execution_options(populate_existing=True).with_for_update())
        if not conversation:
            return None
        setting = UserSettingRepository(self.db).get_by_user(user_id)
        automatic = bool(setting and setting.memory_auto_candidate_enabled)

        assistant = self.db.scalar(select(Message).where(Message.conversation_id == conversation_id,
            Message.id == assistant_message_id, Message.role == "assistant", Message.status == "done")
            .options(load_only(Message.id, Message.role, Message.status, Message.sequence)))
        if not assistant:
            self.db.commit()
            return None
        if setting and not setting.memory_enabled:
            # 关闭期间的对话不能在重新开启后被自动补记；手动提取另有显式入口。
            conversation.memory_scan_cursor = max(conversation.memory_scan_cursor or 0, assistant.sequence or 0)
            conversation.memory_trigger_state = json.dumps({"project_id": conversation.project_id, "pending": []})
            self.db.commit()
            return None
        # 只有旧数据存在空序号时才修复，读取列而非全部历史正文；会话锁仍覆盖此短事务。
        missing_sequence = self.db.scalar(select(Message.id).where(
            Message.conversation_id == conversation_id, Message.sequence.is_(None)).limit(1))
        if missing_sequence:
            ordered = self.db.execute(select(Message.id, Message.sequence).where(
                Message.conversation_id == conversation_id).order_by(Message.created_at,
                Message.sequence.asc().nulls_last(), Message.id)).all()
            maximum = max((row.sequence or 0 for row in ordered), default=0)
            for index, row in enumerate(ordered, 1):
                if not conversation.memory_extraction_cursor or row.sequence is None:
                    maximum += 1
                    value = index if not conversation.memory_extraction_cursor else maximum
                    self.db.execute(update(Message).where(Message.id == row.id).values(sequence=value))
            self.db.flush()
            self.db.refresh(assistant)
        repo = MemoryExtractionJobRepository(self.db)
        outstanding = self.db.scalar(select(MemoryExtractionJob).where(
            MemoryExtractionJob.conversation_id == conversation_id,
            MemoryExtractionJob.status.in_(["pending", "running"])
        ).order_by(MemoryExtractionJob.created_at).limit(1))
        if outstanding:
            self.db.commit()
            return outstanding
        state = read_trigger_state(conversation.memory_trigger_state)
        if state and state.get("project_id") != conversation.project_id:
            # 移动项目不能把旧范围缓冲静默写进新项目；只接收移动后本次的用户输入。
            state = {}
            conversation.memory_scan_cursor = max(0, assistant.sequence - 2)
        pending_ids = [item.get("id") for item in state.get("pending", []) if isinstance(item, dict)][:50]
        pending = list(self.db.scalars(select(Message).where(Message.id.in_(pending_ids),
            Message.conversation_id == conversation_id, Message.role == "user", Message.sequence < assistant.sequence)
            .order_by(Message.sequence))) if pending_ids else []
        cursor = max(conversation.memory_scan_cursor or 0, conversation.memory_extraction_cursor or 0)
        new = list(self.db.scalars(select(Message).where(Message.conversation_id == conversation_id,
            Message.role == "user", Message.sequence > (conversation.memory_extraction_cursor or 0 if force else cursor),
            Message.sequence < assistant.sequence, func.trim(Message.content) != "")
            .order_by(Message.sequence).limit(50 - len(pending)))) if len(pending) < 50 else []
        failed_sources = set()
        if not force:
            # 终态失败留在原 Job 中供人工重试，不能让一个失败来源永久挡住新事实。
            failed_keys = [f"memory-extract:{conversation_id}:{item.id}" for item in [*pending, *new]]
            failures = self.db.scalars(select(MemoryExtractionJob.source_message_ids).where(
                MemoryExtractionJob.conversation_id == conversation_id, MemoryExtractionJob.status == "failed",
                MemoryExtractionJob.idempotency_key.in_(failed_keys)))
            failed_sources = {source_id for row in failures for source_id in row.split(",")}
        signals = []
        seen = set()
        for item in [*pending, *new]:
            if item.id in seen or item.id in failed_sources:
                continue
            seen.add(item.id)
            decision = classify_memory_signal(item.content)
            if (force or decision.action != "skip") and (force or automatic or decision.reason in {"explicit_remember", "explicit_forget"}):
                signals.append((item, decision))
        if new:
            conversation.memory_scan_cursor = max(cursor, max(item.sequence for item in new))
        signals.sort(key=lambda pair: pair[0].sequence)
        # 只存引用与既有来源摘要；每次选取时重新分类，编辑/删除不会继续沿用旧判断。
        state = {"project_id": conversation.project_id, "pending": [
            {"id": item.id, "digest": source_digest(item)} for item, _ in signals]}
        conversation.memory_trigger_state = json.dumps(state)
        interval = max(1, min(int(getattr(setting, "memory_auto_candidate_turn_interval", 4) or 4), 50))
        urgent = max((decision for _, decision in signals), key=lambda decision: decision.priority, default=None)
        if not signals or (not force and not urgent.priority and len(signals) < interval):
            self.db.commit()
            return None
        action = urgent.action if urgent.priority else "extract_now"
        reason = "manual_request" if force else urgent.reason if urgent.priority else "batch_fallback"
        # 撤销独立处理；不得将控制语句和其他事实一起交给提取模型。
        source = [item for item, decision in signals if decision.action == "revoke"][:1] if action == "revoke" else [
            item for item, decision in sorted(signals, key=lambda pair: (-pair[1].priority, pair[0].sequence))
            if decision.action != "revoke"]
        # 优先容纳重要事件，余量按最早来源补齐；未选中的来源留在持久缓冲中。
        batch: list[object] = []
        chars = 0
        for item in source:
            cost = min(len(redact_source(item.content)), 1200) + 80
            if batch and (len(batch) >= 24 or chars + cost > 12000):
                break
            batch.append(item)
            chars += cost
        source = sorted(batch, key=lambda item: item.sequence)
        key = f"memory-extract:{conversation_id}:{source[-1].id}"
        existing = repo.get_by_idempotency_key(key)
        if existing:
            if force and existing.status == "failed":
                # 仅显式重新提取才能重试终态失败；刷新证据快照，不重复创建 Job。
                existing.source_snapshot = source_snapshot(source)
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
            self.db.commit()
            return existing
        job = MemoryExtractionJob(
            user_id=user_id,
            conversation_id=conversation_id,
            project_id=getattr(conversation, "project_id", None),
            idempotency_key=key,
            source_message_ids=",".join(str(getattr(message, "id", "")) for message in source),
            source_snapshot=source_snapshot(source),
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
        try:
            self.db.commit()
            self.db.refresh(job)
            return job
        except IntegrityError:
            self.db.rollback()
            return repo.get_by_idempotency_key(key)


class MemoryCandidateWorker:
    """独立持久化 Worker；模型网络调用期间不持有认领事务。"""

    HEARTBEAT_SECONDS = 20
    LEASE_SECONDS = 90

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

    async def run_once(self) -> bool:
        claimed = self._claim()
        if not claimed:
            from app.services.memory_index_service import MemoryIndexWorker
            return await MemoryIndexWorker(session_factory=self.session_factory).run_once()
        job_id, lease_version = claimed
        from app.services.agent_graph.memory_extraction_graph import run_memory_extraction_graph
        stop, lost = asyncio.Event(), asyncio.Event()
        work = asyncio.create_task(run_memory_extraction_graph(self, job_id, lease_version))
        heartbeat = asyncio.create_task(self._heartbeat_loop(job_id, lease_version, stop, lost, work))
        try:
            await work
        except asyncio.CancelledError:
            # 续租失效只退出旧执行者；正常停机仍向上抛取消，交给租约恢复。
            if not lost.is_set():
                raise
        except Exception as exc:
            self._fail(job_id, lease_version, exc)
        finally:
            stop.set()
            await heartbeat
        return True

    async def _heartbeat_loop(self, job_id, lease_version, stop, lost, work):
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), self.HEARTBEAT_SECONDS)
                return
            except asyncio.TimeoutError:
                pass
            try:
                with self.session_factory() as db:
                    job = db.scalar(select(MemoryExtractionJob).where(MemoryExtractionJob.id == job_id).with_for_update())
                    if job and job.lease_version == lease_version and job.status == "succeeded":
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

    def _verify_candidate_sources(self, job_id, lease_version, suggestions):
        """语义冲突模型前过滤伪造证据，保存时还会锁内重读原文。"""
        with self.session_factory() as db:
            job = db.get(MemoryExtractionJob, job_id)
            if not self._owns(job, lease_version):
                return None
            sources = self._load_sources(db, job)
            return [checked for item in suggestions if (checked := verify_evidence(item, sources)) is not None]

    def _enqueue_backlog(self, job_id: str) -> None:
        """执行期间新增的消息在下一批处理，不等用户再发一轮。"""
        with self.session_factory() as db:
            job = db.get(MemoryExtractionJob, job_id)
            if not job or job.status != "succeeded":
                return
            assistant = db.scalar(select(Message).where(Message.conversation_id == job.conversation_id,
                Message.role == "assistant", Message.status == "done").order_by(
                    Message.created_at.desc(), Message.sequence.desc().nulls_first(), Message.id.desc()).limit(1))
            if assistant:
                MemoryExtractionJobService(db).enqueue_after_turn(user_id=job.user_id,
                    conversation_id=job.conversation_id, assistant_message_id=assistant.id)

    def _claim(self) -> tuple[str, int] | None:
        with self.session_factory() as db:
            job = MemoryExtractionJobRepository(db).claim_next(self.owner)
            if not job:
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
            messages = self._load_sources(db, job)
            recent_text = _source_text(messages)
            if not recent_text:
                raise ValueError("source_messages_missing")
            setting_service = SettingService(UserSettingRepository(db))
            settings = setting_service.get_or_create_user_settings(job.user_id)
            self._check_consent(job, settings)
            if job.trigger_action == "revoke":
                return {"control_request": True}
            if not settings.memory_auto_candidate_enabled and job.trigger_reason == "explicit_remember":
                messages = [item for item in messages if classify_memory_signal(item.content).reason == "explicit_remember"]
                recent_text = _source_text(messages)
            existing_text = MemoryService(
                UserMemoryRepository(db), ConversationRepository(db)
            ).build_existing_memory_text(job.user_id, project_id=job.project_id)
            return {
                "user_id": job.user_id,
                "project_id": job.project_id,
                "conversation_id": job.conversation_id,
                "source_message_ids": job.source_message_ids,
                "provider_call": {
                    "provider_type": settings.provider_type,
                    "base_url": resolve_provider_base_url(
                        provider_type=settings.provider_type,
                        configured_ollama_base_url=settings.ollama_base_url,
                        configured_api_base_url=settings.api_base_url,
                    ),
                    "api_key": setting_service.resolve_provider_api_key(job.user_id),
                    "model_name": settings.default_model,
                    "messages": _candidate_prompt(recent_text=recent_text, existing_text=existing_text),
                    "temperature": 0.1,
                    "top_p": 0.8,
                    "max_tokens": 1600,
                },
            }

    def _complete(self, job_id: str, lease_version: int, suggestions: list) -> bool:
        with self.session_factory() as db:
            job = db.scalars(
                select(MemoryExtractionJob).where(MemoryExtractionJob.id == job_id).with_for_update()
            ).first()
            if not self._owns(job, lease_version):
                return False
            repo = UserMemoryRepository(db)
            with repo.mutation(job.user_id):
                self._persist_results(db, repo, job, suggestions)
            return True

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

    def _persist_results(self, db: Session, repo: UserMemoryRepository, job: MemoryExtractionJob, suggestions: list) -> None:
        # 与手动撤销/更正使用同一用户锁，防止旧 Worker 覆盖用户刚完成的操作。
        conversation = db.scalar(select(Conversation).where(Conversation.id == job.conversation_id,
            Conversation.user_id == job.user_id).execution_options(populate_existing=True).with_for_update())
        if not conversation or conversation.project_id != job.project_id:
            raise ValueError("conversation_scope_changed")
        messages = self._load_sources(db, job)
        settings = db.scalar(select(UserSetting).where(UserSetting.user_id == job.user_id).with_for_update())
        self._check_consent(job, settings)
        service = MemoryService(repo, ConversationRepository(db))
        verified = [verify_evidence(item, messages) for item in suggestions]
        checked = [item.model_copy(update={"project_id": job.project_id}) for item in verified if item]
        if settings and not settings.memory_auto_candidate_enabled and job.trigger_reason == "explicit_remember":
            explicit_ids = {item.id for item in messages if classify_memory_signal(item.content).reason == "explicit_remember"}
            checked = [item for item in checked if item.source_message_id in explicit_ids]
        legacy_enriched = service.enrich_suggestion_risks(
            suggestions=checked,
            existing_memories=[item for item in repo.list_by_user(job.user_id)
                               if service._memory_scope(item, job.user_id) == job.project_id],
            scoped=True,
        )
        enriched = [original if original.conflict_review else legacy for original, legacy in zip(checked, legacy_enriched)]
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
            if repo.find_by_content_hash(job.user_id, content_hash):
                continue
            source = next((item for item in messages if item.id == suggestion.source_message_id), None)
            automatic = bool(job.source_snapshot and settings and settings.memory_enabled
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
            blocked = any(item.status in {"revoked", "rejected", "superseded"}
                and service._memory_scope(item, job.user_id) == job.project_id
                and ((item.fact_key or memory_identity(item.memory_type, item.title, item.content).key) == memory.fact_key
                     or equivalent_content(item.content, memory.content)
                     or bool(set((item.source_message_ids or "").split(",")) & set(job.source_message_ids.split(","))))
                for item in repo.list_by_user(job.user_id))
            if blocked or not suggestion.evidence_verified:
                memory.is_enabled = False
                memory.status = "pending"
                memory.source = "auto_candidate"
                if memory.risk_level == "safe":
                    memory.risk_level = "review_required"
                memory.candidate_reason = "用户已有撤销/替换历史，需重新确认" if blocked else "缺少可核验的逐条用户原文，需人工审核"
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
            created += 1
        self._finish_job(conversation, job, created)

    @staticmethod
    def _check_consent(job, settings):
        if settings and not settings.memory_enabled:
            raise PermissionError("memory_disabled")
        if (job.trigger_reason not in {"explicit_remember", "explicit_forget", "manual_request", "legacy_batch"}
                and not (settings and settings.memory_auto_candidate_enabled)):
            raise PermissionError("automatic_extraction_disabled")

    @staticmethod
    def _finish_job(conversation, job, count):
        """任务、结果和缓冲消费同事务；失败或旧租约不能吃掉来源。"""
        job.status = "succeeded"
        job.result_count = count
        job.finished_at = utcnow()
        job.lease_owner = None
        job.lease_expires_at = None
        job.error_code = None
        job.error_message = None
        conversation.memory_extraction_cursor = max(conversation.memory_extraction_cursor or 0, job.cursor_end or 0)
        state = read_trigger_state(conversation.memory_trigger_state)
        state["project_id"] = conversation.project_id
        consumed = set(job.source_message_ids.split(","))
        state["pending"] = [item for item in state.get("pending", []) if item.get("id") not in consumed]
        conversation.memory_trigger_state = json.dumps(state)

    def _complete_control(self, job_id, lease_version):
        """撤销仅接受原始用户明确命令和唯一精确目标；不调用模型猜目标。"""
        with self.session_factory() as db:
            job = db.scalar(select(MemoryExtractionJob).where(MemoryExtractionJob.id == job_id).with_for_update())
            if not self._owns(job, lease_version):
                return False
            repo = UserMemoryRepository(db)
            with repo.mutation(job.user_id):
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
                job.finished_at = utcnow()
                job.error_code = "invalid_source" if isinstance(exc, ValueError) else "extraction_failed"
                job.error_message = "长期记忆提取失败，本次未保存候选或推进游标。"
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
