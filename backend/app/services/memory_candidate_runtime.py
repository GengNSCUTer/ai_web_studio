from __future__ import annotations

import socket
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.user_memory import MemoryExtractionJob, UserMemory
from app.models.conversation import Conversation
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
    REDACTED, allows_automatic, explicit_remember, extraction_prompt,
    redact_source, source_digest, source_snapshot, verify_evidence,
)
from app.services.setting_service import SettingService


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
        if not force and (not setting or not getattr(setting, "memory_auto_candidate_enabled", False)):
            return None

        messages = MessageRepository(self.db).list_by_conversation(conversation_id)
        assistant = next((item for item in messages if item.id == assistant_message_id
                          and item.role == "assistant" and item.status == "done"), None)
        if not assistant:
            return None
        # 混合旧数据首次提取按历史顺序补齐，避免旧消息放到末尾跳过后续新增消息。
        if any(item.sequence is None for item in messages) and not conversation.memory_extraction_cursor:
            for sequence, item in enumerate(messages, start=1):
                item.sequence = sequence
        else:
            max_sequence = max((item.sequence or 0 for item in messages), default=0)
            for item in messages:
                if item.sequence is None:
                    max_sequence += 1
                    item.sequence = max_sequence
        source = sorted(_source_messages(messages[:messages.index(assistant)]), key=lambda item: item.sequence)
        source = [item for item in source if item.sequence > (conversation.memory_extraction_cursor or 0)]
        repo = MemoryExtractionJobRepository(self.db)
        outstanding = self.db.scalar(select(MemoryExtractionJob).where(
            MemoryExtractionJob.conversation_id == conversation_id,
            MemoryExtractionJob.status.in_(["pending", "running"])
        ).order_by(MemoryExtractionJob.created_at).limit(1))
        if outstanding:
            self.db.commit()
            return outstanding
        if not source:
            self.db.commit()
            return None
        interval = max(1, min(int(getattr(setting, "memory_auto_candidate_turn_interval", 4) or 4), 50))
        if not force and len(source) < interval and not any(explicit_remember(item.content) for item in source):
            self.db.commit()
            return None
        # 限制批次，不再只取尾部 24 条；积压从最早未处理消息逐批推进。
        batch: list[object] = []
        chars = 0
        for item in source:
            cost = min(len(redact_source(item.content)), 1200) + 80
            if batch and (len(batch) >= 24 or chars + cost > 12000):
                break
            batch.append(item)
            chars += cost
        source = batch
        key = f"memory-extract:{conversation_id}:{source[-1].id}"
        existing = repo.get_by_idempotency_key(key)
        if existing:
            if force and existing.status == "failed":
                # 仅显式重新提取才能重试终态失败；刷新证据快照，不重复创建 Job。
                existing.source_snapshot = source_snapshot(source)
                existing.cursor_end = source[-1].sequence
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
            status="pending",
            available_at=utcnow(),
        )
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

    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
        owner: str | None = None,
        provider_service: ChatProviderService | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.owner = owner or f"memory-worker-{socket.gethostname()}-{id(self)}"
        self.provider = provider_service or ChatProviderService()

    async def run_once(self) -> bool:
        claimed = self._claim()
        if not claimed:
            return False
        job_id, lease_version = claimed
        try:
            snapshot = self._load_snapshot(job_id, lease_version)
            if snapshot is None:
                return True
            raw = await self.provider.complete_chat(**snapshot["provider_call"])
            # 非法输出不能冒充“没有记忆”，否则游标推进后该批消息永远丢失。
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
            suggestions = MemoryService.parse_suggestion_json(
                raw,
                max_candidates=5,
                source_conversation_id=snapshot["conversation_id"],
                source_message_ids=snapshot["source_message_ids"],
            )
            self._complete(job_id, lease_version, suggestions)
            self._enqueue_backlog(job_id)
        except Exception as exc:
            self._fail(job_id, lease_version, exc)
        return True

    def _enqueue_backlog(self, job_id: str) -> None:
        """执行期间新增的消息在下一批处理，不等用户再发一轮。"""
        with self.session_factory() as db:
            job = db.get(MemoryExtractionJob, job_id)
            if not job or job.status != "succeeded":
                return
            messages = MessageRepository(db).list_by_conversation(job.conversation_id)
            assistant = next((item for item in reversed(messages) if item.role == "assistant"
                              and item.status == "done"), None)
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
            job.lease_expires_at = now + timedelta(seconds=90)
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
            messages = self._load_sources(db, job)
            recent_text = _source_text(messages)
            if not recent_text:
                raise ValueError("source_messages_missing")
            setting_service = SettingService(UserSettingRepository(db))
            settings = setting_service.get_or_create_user_settings(job.user_id)
            existing_text = MemoryService(
                UserMemoryRepository(db), ConversationRepository(db)
            ).build_existing_memory_text(job.user_id, project_id=job.project_id)
            return {
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

    def _complete(self, job_id: str, lease_version: int, suggestions: list) -> None:
        with self.session_factory() as db:
            job = db.scalars(
                select(MemoryExtractionJob).where(MemoryExtractionJob.id == job_id).with_for_update()
            ).first()
            if not self._owns(job, lease_version):
                return
            repo = UserMemoryRepository(db)
            with repo.mutation(job.user_id):
                self._persist_results(db, repo, job, suggestions)

    def _load_sources(self, db: Session, job: MemoryExtractionJob) -> list:
        ids = set(job.source_message_ids.split(","))
        messages = [item for item in MessageRepository(db).list_by_conversation(job.conversation_id)
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
        service = MemoryService(repo, ConversationRepository(db))
        verified = [verify_evidence(item, messages) for item in suggestions]
        enriched = service.enrich_suggestion_risks(
            suggestions=[item.model_copy(update={"project_id": job.project_id}) for item in verified if item],
            existing_memories=[item for item in repo.list_by_user(job.user_id)
                               if service._memory_scope(item, job.user_id) == job.project_id],
            scoped=True,
        )
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
                and allows_automatic(suggestion, source))
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
                    memory.candidate_reason = "保存时发现事实冲突，需用户确认"
                else:
                    memory.review_at = utcnow()
            repo.flush(memory)
            created += 1
        job.status = "succeeded"
        job.result_count = created
        job.finished_at = utcnow()
        job.lease_owner = None
        job.lease_expires_at = None
        job.error_code = None
        job.error_message = None
        conversation.memory_extraction_cursor = max(conversation.memory_extraction_cursor or 0, job.cursor_end or 0)

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
        )
