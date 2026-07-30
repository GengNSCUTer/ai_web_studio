from __future__ import annotations

import socket
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import SessionLocal
from app.models.user_memory import MemoryExtractionJob, UserMemory
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.memory_job_repo import MemoryExtractionJobRepository
from app.repositories.memory_repo import UserMemoryRepository
from app.repositories.message_repo import MessageRepository
from app.repositories.setting_repo import UserSettingRepository
from app.services.chat_provider_service import ChatProviderService, resolve_provider_base_url
from app.services.memory_service import MemoryService
from app.services.setting_service import SettingService


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _source_messages(messages: list[object]) -> list[object]:
    return [
        message
        for message in messages[-24:]
        if getattr(message, "role", "") in {"user", "assistant"}
        and (getattr(message, "content", None) or "").strip()
    ]


def _source_text(messages: list[object], *, max_chars: int = 12000) -> str:
    lines: list[str] = []
    total = 0
    for message in _source_messages(messages):
        content = " ".join((getattr(message, "content", None) or "").split()).strip()[:1200]
        line = f"{getattr(message, 'role', '')}: {content}"
        if total + len(line) > max_chars:
            break
        lines.append(line)
        total += len(line)
    return "\n".join(lines)


def _candidate_prompt(*, recent_text: str, existing_text: str) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "你是长期记忆候选提取器。只提取用户明确表达、跨会话仍有价值且可验证的信息。"
                "禁止提取密码、API Key、Token、身份证号、手机号、邮箱等秘密或直接个人标识。"
                "你只能生成候选，不能激活、修改或删除已有记忆，也不能调用工具。"
            ),
        },
        {
            "role": "user",
            "content": f"""从最近对话提取最多 5 条候选长期记忆，输出严格 JSON 数组。

类型仅允许 profile、project、fact、instruction。每项字段：memory_type、title、content、reason、confidence。
confidence 仅允许 high、medium、low。不要输出 Markdown 或解释；不确定、临时、推测和已有重复内容不要提取。

【已有记忆】
{existing_text}

【最近对话】
{recent_text}
""",
        },
    ]


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
        conversation = ConversationRepository(self.db).get_by_user(conversation_id, user_id)
        if not conversation:
            return None
        setting = UserSettingRepository(self.db).get_by_user(user_id)
        if not force and (not setting or not getattr(setting, "memory_auto_candidate_enabled", False)):
            return None

        messages = MessageRepository(self.db).list_by_conversation(conversation_id)
        source = _source_messages(messages)
        if not source:
            return None
        user_turns = sum(1 for message in messages if getattr(message, "role", None) == "user")
        interval = max(1, min(int(getattr(setting, "memory_auto_candidate_turn_interval", 4) or 4), 50))
        if not force and user_turns % interval != 0:
            return None

        key = f"memory-extract:{conversation_id}:{assistant_message_id}"
        repo = MemoryExtractionJobRepository(self.db)
        existing = repo.get_by_idempotency_key(key)
        if existing:
            return existing
        job = MemoryExtractionJob(
            user_id=user_id,
            conversation_id=conversation_id,
            project_id=getattr(conversation, "project_id", None),
            idempotency_key=key,
            source_message_ids=",".join(str(getattr(message, "id", "")) for message in source),
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
    """PostgreSQL-backed worker; LLM I/O happens outside the lease-claim transaction."""

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
            suggestions = MemoryService.parse_suggestion_json(
                raw,
                max_candidates=5,
                source_conversation_id=snapshot["conversation_id"],
                source_message_ids=snapshot["source_message_ids"],
            )
            self._complete(job_id, lease_version, suggestions)
        except Exception as exc:
            self._fail(job_id, lease_version, exc)
        return True

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
            source_ids = {item for item in job.source_message_ids.split(",") if item}
            messages = [
                message
                for message in MessageRepository(db).list_by_conversation(job.conversation_id)
                if str(getattr(message, "id", "")) in source_ids
            ]
            recent_text = _source_text(messages)
            if not recent_text:
                raise ValueError("source_messages_missing")
            setting_service = SettingService(UserSettingRepository(db))
            settings = setting_service.get_or_create_user_settings(job.user_id)
            existing_text = MemoryService(
                UserMemoryRepository(db), ConversationRepository(db)
            ).build_existing_memory_text(job.user_id)
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
            service = MemoryService(repo, ConversationRepository(db))
            enriched = service.enrich_suggestion_risks(
                suggestions=suggestions,
                existing_memories=repo.list_by_user(job.user_id),
            )
            created = 0
            for suggestion in enriched:
                content_hash = service.memory_content_hash(
                    user_id=job.user_id,
                    memory_type=suggestion.memory_type,
                    content=suggestion.content,
                    project_id=job.project_id if suggestion.memory_type == "project" else None,
                )
                if repo.find_by_content_hash(job.user_id, content_hash):
                    continue
                memory = UserMemory(
                    user_id=job.user_id,
                    memory_type=suggestion.memory_type,
                    title=suggestion.title,
                    content=suggestion.content,
                    source="auto_candidate",
                    source_conversation_id=job.conversation_id,
                    source_message_ids=job.source_message_ids,
                    confidence=suggestion.confidence,
                    is_enabled=False,
                    status="pending",
                    project_id=job.project_id if suggestion.memory_type == "project" else None,
                    importance={"high": 0.9, "medium": 0.6, "low": 0.3}.get(suggestion.confidence or "", 0.5),
                    sensitivity="sensitive" if suggestion.risk_level == "sensitive" else "normal",
                    risk_level=suggestion.risk_level,
                    candidate_reason=suggestion.risk_reason or suggestion.reason,
                    content_hash=content_hash,
                    supersedes_memory_id=suggestion.conflict_memory_id,
                )
                repo.flush(memory)
                created += 1
            job.status = "succeeded"
            job.result_count = created
            job.finished_at = utcnow()
            job.lease_owner = None
            job.lease_expires_at = None
            job.error_code = None
            job.error_message = None
            db.commit()

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
                job.error_message = "长期记忆候选提取失败，未写入任何 active 记忆。"
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
