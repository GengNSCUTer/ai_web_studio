from __future__ import annotations

import json
import logging
import socket
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Callable

from redis import Redis
from redis.exceptions import ResponseError
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.knowledge import (
    KnowledgeBase,
    KnowledgeDocument,
    KnowledgeIndexGeneration,
    KnowledgeJob,
    OutboxEvent,
)
from app.repositories.knowledge_repo import KnowledgeChunkRepository, KnowledgeDocumentRepository
from app.repositories.setting_repo import UserSettingRepository
from app.repositories.tool_config_repo import ToolConfigRepository
from app.services.knowledge_index_service import IndexResult, KnowledgeIndexService
from app.services.knowledge_parser_service import KnowledgeParserService, ParseResult
from app.services.secret_service import SecretService
from app.services.setting_service import SettingService
from app.services.tools.credentials import ToolCredential


logger = logging.getLogger(__name__)

TERMINAL_JOB_STATUSES = {"succeeded", "failed", "dead_letter"}
RETRYABLE_ERROR_CODES = {"provider_unavailable", "job_failed"}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def classify_job_error(exc: Exception) -> str:
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return "provider_unavailable"
    if isinstance(exc, (PermissionError, FileNotFoundError, ValueError)):
        return "invalid_request"
    message = str(exc).lower()
    if any(marker in message for marker in ("timeout", "timed out", "rate limit", "429", "502", "503")):
        return "provider_unavailable"
    return "job_failed"


def safe_error_message(error_code: str) -> str:
    return {
        "provider_unavailable": "外部服务暂时不可用，任务将按有限退避策略重试。",
        "invalid_request": "任务输入或资源状态不合法，已停止自动重试。",
        "job_failed": "任务执行失败，将在达到最大尝试次数前重试。",
    }.get(error_code, "任务执行失败。")


@dataclass(frozen=True)
class JobLease:
    job_id: str
    owner: str
    version: int
    job_type: str


@dataclass(frozen=True)
class ClaimResult:
    state: str
    lease: JobLease | None = None


class FrozenCredentialResolver:
    def __init__(self, credential: ToolCredential) -> None:
        self.credential = credential

    def resolve(self, *, user_id: str | None, provider_key: str) -> ToolCredential:  # noqa: ARG002
        if provider_key != self.credential.provider_key:
            return ToolCredential(provider_key=provider_key, api_key=None, source="missing", is_enabled=False)
        return self.credential


class FrozenSettingService:
    """Read-only credential/config snapshot so provider I/O does not hold a DB transaction."""

    def __init__(self, setting: Any, embedding_api_key: str | None, rerank_api_key: str | None) -> None:
        self.setting = setting
        self.embedding_api_key = embedding_api_key
        self.rerank_api_key = rerank_api_key

    def get_or_create_user_settings(self, user_id: str) -> Any:  # noqa: ARG002
        return self.setting

    def resolve_knowledge_model_api_key(self, user_id: str, model_kind: str) -> str | None:  # noqa: ARG002
        return self.rerank_api_key if model_kind == "rerank" else self.embedding_api_key


class KnowledgeOutboxPublisher:
    def __init__(
        self,
        redis_client: Redis,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
        publisher_id: str | None = None,
    ) -> None:
        self.redis = redis_client
        self.session_factory = session_factory
        self.publisher_id = publisher_id or f"publisher-{socket.gethostname()}-{id(self)}"

    def publish_once(self) -> bool:
        now = utcnow()
        with self.session_factory() as db:
            statement = (
                select(OutboxEvent)
                .where(
                    OutboxEvent.available_at <= now,
                    or_(
                        OutboxEvent.status == "pending",
                        (OutboxEvent.status == "publishing") & (OutboxEvent.lease_expires_at < now),
                    ),
                )
                .order_by(OutboxEvent.created_at.asc())
                .with_for_update(skip_locked=True)
                .limit(1)
            )
            event = db.scalars(statement).first()
            if not event:
                return False
            event.status = "publishing"
            event.lease_owner = self.publisher_id
            event.lease_expires_at = now + timedelta(seconds=30)
            event.attempt_count = (event.attempt_count or 0) + 1
            event_id = event.id
            event_key = event.event_key
            aggregate_id = event.aggregate_id
            db.commit()

        try:
            self.redis.xadd(
                settings.knowledge_job_stream,
                {"event_id": event_key, "job_id": aggregate_id},
            )
        except Exception as exc:
            with self.session_factory() as db:
                event = db.get(OutboxEvent, event_id)
                if event and event.status == "publishing" and event.lease_owner == self.publisher_id:
                    event.status = "pending"
                    event.available_at = utcnow() + timedelta(seconds=min(30, 2 ** min(event.attempt_count, 5)))
                    event.lease_owner = None
                    event.lease_expires_at = None
                    event.error_message = type(exc).__name__
                    db.commit()
            raise

        # Redis XADD 成功、PostgreSQL 标记前宕机会重发；稳定 event_id + Job Lease 使重发无害。
        with self.session_factory() as db:
            event = db.get(OutboxEvent, event_id)
            if event and event.status == "publishing" and event.lease_owner == self.publisher_id:
                event.status = "published"
                event.published_at = utcnow()
                event.lease_owner = None
                event.lease_expires_at = None
                event.error_message = None
                db.commit()
        return True


class KnowledgeJobCoordinator:
    def __init__(
        self,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
        lease_seconds: int | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.lease_seconds = lease_seconds or settings.knowledge_job_lease_seconds

    def claim(self, job_id: str, owner: str) -> ClaimResult:
        now = utcnow()
        with self.session_factory() as db:
            job = db.scalars(select(KnowledgeJob).where(KnowledgeJob.id == job_id).with_for_update()).first()
            if not job:
                return ClaimResult("missing")
            if job.status in TERMINAL_JOB_STATUSES:
                return ClaimResult("terminal")
            if job.available_at and _as_utc(job.available_at) > now:
                return ClaimResult("deferred")
            if (
                job.status == "running"
                and job.lease_expires_at
                and _as_utc(job.lease_expires_at) > now
                and job.lease_owner != owner
            ):
                return ClaimResult("busy")
            job.status = "running"
            job.retry_count = (job.retry_count or 0) + 1
            job.lease_owner = owner
            job.lease_version = (job.lease_version or 0) + 1
            job.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
            job.heartbeat_at = now
            job.started_at = job.started_at or now
            job.error_code = None
            job.error_message = None
            version = job.lease_version
            job_type = job.job_type
            db.commit()
            return ClaimResult("claimed", JobLease(job_id, owner, version, job_type))

    def heartbeat(self, lease: JobLease) -> bool:
        now = utcnow()
        with self.session_factory() as db:
            job = db.scalars(select(KnowledgeJob).where(KnowledgeJob.id == lease.job_id).with_for_update()).first()
            if not _owns_lease(job, lease):
                return False
            job.heartbeat_at = now
            job.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
            db.commit()
            return True

    def complete_parse(self, lease: JobLease, result: ParseResult) -> bool:
        with self.session_factory() as db:
            job = db.scalars(select(KnowledgeJob).where(KnowledgeJob.id == lease.job_id).with_for_update()).first()
            if not _owns_lease(job, lease) or not job or not job.document_id:
                return False
            document = db.scalars(
                select(KnowledgeDocument).where(KnowledgeDocument.id == job.document_id).with_for_update()
            ).first()
            if not document:
                return False
            document.parse_status = "parsed"
            document.index_status = "pending"
            document.parsed_markdown_path = result.markdown_path
            document.parsed_assets_json = result.assets_json
            document.error_message = None
            _mark_succeeded(job, {"markdown_path": result.markdown_path})
            db.commit()
            return True

    def activate_generation(self, lease: JobLease, generation_id: str, result: IndexResult) -> bool:
        with self.session_factory() as db:
            job = db.scalars(select(KnowledgeJob).where(KnowledgeJob.id == lease.job_id).with_for_update()).first()
            if not _owns_lease(job, lease) or not job or not job.document_id:
                return False
            knowledge_base = db.scalars(
                select(KnowledgeBase).where(KnowledgeBase.id == job.knowledge_base_id).with_for_update()
            ).first()
            generation = db.scalars(
                select(KnowledgeIndexGeneration)
                .where(KnowledgeIndexGeneration.id == generation_id)
                .with_for_update()
            ).first()
            document = db.scalars(
                select(KnowledgeDocument).where(KnowledgeDocument.id == job.document_id).with_for_update()
            ).first()
            if not knowledge_base or not generation or not document:
                return False
            if (knowledge_base.active_index_generation or "legacy") != (generation.base_generation_id or "legacy"):
                generation.status = "superseded"
                generation.error_message = "active generation changed before CAS activation"
                db.commit()
                return False
            knowledge_base.active_index_generation = generation.id
            generation.status = "active"
            generation.activated_at = utcnow()
            generation.manifest_hash = result.manifest_hash
            generation.chunk_count = result.chunk_count
            document.index_status = "indexed"
            document.error_message = None
            _mark_succeeded(
                job,
                {
                    "generation_id": generation.id,
                    "chunk_count": result.chunk_count,
                    "index_path": result.index_path,
                    "manifest_hash": result.manifest_hash,
                },
            )
            db.commit()
            return True

    def fail(self, lease: JobLease, exc: Exception, generation_id: str | None = None) -> bool:
        now = utcnow()
        error_code = classify_job_error(exc)
        with self.session_factory() as db:
            job = db.scalars(select(KnowledgeJob).where(KnowledgeJob.id == lease.job_id).with_for_update()).first()
            if not _owns_lease(job, lease) or not job:
                return False
            if generation_id:
                generation = db.get(KnowledgeIndexGeneration, generation_id)
                if generation and generation.status != "active":
                    generation.status = "failed"
                    generation.error_message = safe_error_message(error_code)
            retryable = error_code in RETRYABLE_ERROR_CODES and job.retry_count < (job.max_attempts or 3)
            job.error_code = error_code
            job.error_message = safe_error_message(error_code)
            job.lease_owner = None
            job.lease_expires_at = None
            job.heartbeat_at = None
            if retryable:
                delay_seconds = min(60, 2 ** max(1, job.retry_count))
                job.status = "pending"
                job.available_at = now + timedelta(seconds=delay_seconds)
                db.add(
                    OutboxEvent(
                        event_key=f"knowledge-job:{job.id}:attempt:{job.retry_count + 1}",
                        aggregate_id=job.id,
                        payload_json=json.dumps(
                            {"event_id": f"knowledge-job:{job.id}:attempt:{job.retry_count + 1}", "job_id": job.id},
                            ensure_ascii=False,
                        ),
                        status="pending",
                        available_at=job.available_at,
                    )
                )
            else:
                job.status = "dead_letter"
                job.available_at = None
                job.finished_at = now
                job.dead_lettered_at = now
                document = db.get(KnowledgeDocument, job.document_id) if job.document_id else None
                if document:
                    if job.job_type == "parse_document":
                        document.parse_status = "failed"
                    elif job.job_type == "index_document":
                        document.index_status = "failed"
                    document.error_message = job.error_message
            db.commit()
            return True


class KnowledgeJobWorker:
    def __init__(
        self,
        redis_client: Redis,
        *,
        session_factory: Callable[[], Session] = SessionLocal,
        worker_id: str | None = None,
    ) -> None:
        self.redis = redis_client
        self.session_factory = session_factory
        self.worker_id = worker_id or f"worker-{socket.gethostname()}-{id(self)}"
        self.coordinator = KnowledgeJobCoordinator(session_factory=session_factory)
        self.publisher = KnowledgeOutboxPublisher(redis_client, session_factory=session_factory)

    def ensure_group(self) -> None:
        try:
            self.redis.xgroup_create(
                settings.knowledge_job_stream,
                settings.knowledge_job_group,
                id="0-0",
                mkstream=True,
            )
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

    def run_forever(self) -> None:
        self.ensure_group()
        logger.info("knowledge worker started: %s", self.worker_id)
        while True:
            try:
                while self.publisher.publish_once():
                    pass
                self._recover_stale_messages()
                messages = self.redis.xreadgroup(
                    settings.knowledge_job_group,
                    self.worker_id,
                    {settings.knowledge_job_stream: ">"},
                    count=5,
                    block=2000,
                )
                for _, records in messages or []:
                    for message_id, fields in records:
                        self.process_message(message_id, fields)
            except KeyboardInterrupt:
                raise
            except Exception:
                logger.exception("knowledge worker loop failed")
                time.sleep(1)

    def _recover_stale_messages(self) -> None:
        claimed = self.redis.xautoclaim(
            settings.knowledge_job_stream,
            settings.knowledge_job_group,
            self.worker_id,
            min_idle_time=settings.knowledge_job_claim_idle_ms,
            start_id="0-0",
            count=10,
        )
        records = claimed[1] if claimed and len(claimed) > 1 else []
        for message_id, fields in records:
            self.process_message(message_id, fields)

    def process_message(self, message_id: Any, fields: dict[Any, Any]) -> str:
        decoded = {_decode(key): _decode(value) for key, value in fields.items()}
        job_id = decoded.get("job_id")
        if not job_id:
            self._ack(message_id)
            return "invalid"
        claim = self.coordinator.claim(job_id, self.worker_id)
        if claim.state in {"missing", "terminal", "busy"}:
            self._ack(message_id)
            return claim.state
        if claim.state == "deferred" or not claim.lease:
            return claim.state

        lease = claim.lease
        stop_heartbeat = threading.Event()
        lost_lease = threading.Event()
        heartbeat = threading.Thread(
            target=self._heartbeat_loop,
            args=(lease, stop_heartbeat, lost_lease),
            daemon=True,
        )
        heartbeat.start()
        generation_id: str | None = None
        try:
            if lease.job_type == "parse_document":
                result = self._execute_parse(lease)
                completed = not lost_lease.is_set() and self.coordinator.complete_parse(lease, result)
            elif lease.job_type == "index_document":
                generation_id, result = self._execute_index(lease)
                completed = not lost_lease.is_set() and self.coordinator.activate_generation(
                    lease,
                    generation_id,
                    result,
                )
            else:
                raise ValueError(f"unsupported knowledge job type: {lease.job_type}")
            if completed:
                self._ack(message_id)
                return "succeeded"
            return "lease_lost"
        except Exception as exc:
            transitioned = self.coordinator.fail(lease, exc, generation_id=generation_id)
            if transitioned:
                self._ack(message_id)
                return "retry_or_dead_letter"
            return "lease_lost"
        finally:
            stop_heartbeat.set()
            heartbeat.join(timeout=2)

    def _heartbeat_loop(self, lease: JobLease, stop: threading.Event, lost: threading.Event) -> None:
        interval = max(1.0, self.coordinator.lease_seconds / 3)
        while not stop.wait(interval):
            try:
                if not self.coordinator.heartbeat(lease):
                    lost.set()
                    return
            except Exception:
                logger.exception("knowledge job heartbeat failed: %s", lease.job_id)

    def _execute_parse(self, lease: JobLease) -> ParseResult:
        with self.session_factory() as db:
            job = db.get(KnowledgeJob, lease.job_id)
            document = db.get(KnowledgeDocument, job.document_id) if job and job.document_id else None
            if not job or not document:
                raise ValueError("knowledge parse job references a missing document")
            user_id = job.user_id
            document_snapshot = _document_snapshot(document)
            credential = _resolve_tool_credential(db, user_id, "mineru")
            db.rollback()
        parser = KnowledgeParserService(credential_resolver=FrozenCredentialResolver(credential))
        return parser.parse(document=document_snapshot, user_id=user_id)

    def _execute_index(self, lease: JobLease) -> tuple[str, IndexResult]:
        with self.session_factory() as db:
            job = db.get(KnowledgeJob, lease.job_id)
            knowledge_base = db.get(KnowledgeBase, job.knowledge_base_id) if job else None
            document = db.get(KnowledgeDocument, job.document_id) if job and job.document_id else None
            if not job or not knowledge_base or not document:
                raise ValueError("knowledge index job references missing state")
            user_id = job.user_id
            knowledge_base_id = job.knowledge_base_id
            generation_id = f"{job.id}-v{lease.version}"
            generation = db.get(KnowledgeIndexGeneration, generation_id)
            if not generation:
                generation = KnowledgeIndexGeneration(
                    id=generation_id,
                    user_id=user_id,
                    knowledge_base_id=knowledge_base_id,
                    base_generation_id=knowledge_base.active_index_generation or "legacy",
                    job_id=job.id,
                    status="building",
                )
                db.add(generation)
                db.commit()
            setting_service = SettingService(UserSettingRepository(db))
            setting_snapshot = setting_service.get_or_create_user_settings(user_id)
            embedding_key = setting_service.resolve_knowledge_model_api_key(user_id, "embedding")
            rerank_key = setting_service.resolve_knowledge_model_api_key(user_id, "rerank")
            frozen_settings = FrozenSettingService(setting_snapshot, embedding_key, rerank_key)
            base_snapshot = _knowledge_base_snapshot(knowledge_base)
            document_snapshot = _index_document_snapshot(document)
            db.commit()
            index_service = KnowledgeIndexService(
                chunk_repo=KnowledgeChunkRepository(db),
                document_repo=KnowledgeDocumentRepository(db),
                setting_service=frozen_settings,  # type: ignore[arg-type]
            )
            result = index_service.build_inactive_generation(
                user_id=user_id,
                knowledge_base=base_snapshot,
                document=document_snapshot,
                generation_id=generation_id,
            )
            generation = db.get(KnowledgeIndexGeneration, generation_id)
            if not generation:
                raise RuntimeError("index generation disappeared before validation")
            generation.status = "ready"
            generation.manifest_hash = result.manifest_hash
            generation.chunk_count = result.chunk_count
            db.commit()
            return generation_id, result

    def _ack(self, message_id: Any) -> None:
        self.redis.xack(settings.knowledge_job_stream, settings.knowledge_job_group, message_id)


def create_knowledge_worker() -> KnowledgeJobWorker:
    client = Redis.from_url(settings.knowledge_redis_url, decode_responses=True)
    return KnowledgeJobWorker(client)


def _mark_succeeded(job: KnowledgeJob, result: dict[str, Any]) -> None:
    job.status = "succeeded"
    job.result_json = json.dumps(result, ensure_ascii=False)
    job.error_code = None
    job.error_message = None
    job.available_at = None
    job.lease_owner = None
    job.lease_expires_at = None
    job.heartbeat_at = None
    job.finished_at = utcnow()


def _owns_lease(job: KnowledgeJob | None, lease: JobLease) -> bool:
    return bool(
        job
        and job.status == "running"
        and job.lease_owner == lease.owner
        and job.lease_version == lease.version
        and job.lease_expires_at is not None
        and _as_utc(job.lease_expires_at) > utcnow()
    )


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _decode(value: Any) -> str:
    return value.decode("utf-8") if isinstance(value, bytes) else str(value)


def _document_snapshot(document: KnowledgeDocument) -> Any:
    return SimpleNamespace(
        id=document.id,
        user_id=document.user_id,
        knowledge_base_id=document.knowledge_base_id,
        storage_key=document.storage_key,
        parser_provider=document.parser_provider,
        file_name=document.file_name,
        mime_type=document.mime_type,
    )


def _index_document_snapshot(document: KnowledgeDocument) -> Any:
    return SimpleNamespace(
        id=document.id,
        user_id=document.user_id,
        knowledge_base_id=document.knowledge_base_id,
        storage_key=document.storage_key,
        parser_provider=document.parser_provider,
        file_name=document.file_name,
        mime_type=document.mime_type,
        parse_status=document.parse_status,
        parsed_markdown_path=document.parsed_markdown_path,
        document_version=document.document_version,
    )


def _knowledge_base_snapshot(knowledge_base: KnowledgeBase) -> Any:
    fields = (
        "id",
        "user_id",
        "active_index_generation",
        "chunk_mode",
        "chunk_size",
        "chunk_overlap",
        "parent_chunk_size",
        "child_chunk_size",
        "child_chunk_overlap",
        "embedding_provider",
        "embedding_model",
        "embedding_dimensions",
    )
    return SimpleNamespace(**{field: getattr(knowledge_base, field) for field in fields})


def _resolve_tool_credential(db: Session, user_id: str, provider_key: str) -> ToolCredential:
    credential = ToolConfigRepository(db).get_credential(user_id, provider_key)
    if credential and credential.is_enabled:
        api_key = SecretService().decrypt(credential.api_key)
        return ToolCredential(provider_key=provider_key, api_key=api_key, source="user", is_enabled=bool(api_key))
    return ToolCredential(provider_key=provider_key, api_key=None, source="missing", is_enabled=False)
