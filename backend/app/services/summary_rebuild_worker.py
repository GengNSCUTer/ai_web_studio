"""有界摘要重建：每批短事务提交，模型等待不持有连接或会话锁。"""

import asyncio
from datetime import timedelta
import random
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import or_, select
import httpx
from openai import APIConnectionError

from app.models.conversation import Conversation
from app.models.conversation_summary import ConversationSummaryJob as Job
from app.models.message import Message
from app.models.user import User
from app.repositories.message_repo import MessageRepository
from app.services.chat_context_assembly_service import build_summary_prompt
from app.services.chat_execution_service import ChatExecutionService
from app.services.chat_provider_service import ChatProviderService
from app.services.conversation_summary_service import ConversationSummaryService, select_summary_batch, summary_source_budget
from app.services.runtime_lease import aware, owns_live_lease, utcnow, watch_lease


class SummaryRebuildWorker:
    LEASE_SECONDS = 180
    HEARTBEAT_SECONDS = 20
    MODEL_TIMEOUT_SECONDS = 90

    def __init__(self, *, session_factory, owner=None, provider_factory=ChatProviderService):
        self.sessions = session_factory
        self.owner = owner or f"summary-worker:{uuid4()}"
        self.provider_factory = provider_factory
        self.stop = asyncio.Event()
        self.active = None

    def request_stop(self):
        self.stop.set()
        if self.active:
            self.active.cancel()

    def claim(self):
        with self.sessions() as db:
            now = utcnow()
            candidates = list(db.execute(select(Job.id, Job.conversation_id).where(or_(
                (Job.status == "queued") & or_(Job.next_attempt_at.is_(None), Job.next_attempt_at <= now),
                (Job.status == "running") & (Job.lease_expires_at <= now)))
                .order_by(Job.created_at, Job.id).limit(16)))
            for jid, cid in candidates:
                # 所有需要两行的路径统一先会话、后Job，避免与历史删除锁顺序相反。
                conv = db.scalar(select(Conversation).where(Conversation.id == cid).with_for_update(skip_locked=True))
                if not conv:
                    continue
                job = db.scalar(select(Job).where(Job.id == jid).with_for_update(skip_locked=True))
                if not job:
                    db.rollback()
                    continue
                if job.status == "queued" and job.next_attempt_at and aware(job.next_attempt_at) > utcnow():
                    db.rollback()
                    continue
                if job.status not in {"queued", "running"} or (job.status == "running" and job.lease_expires_at
                        and owns_live_lease(job, owner=job.lease_owner, version=job.lease_version)):
                    db.rollback()
                    continue
                if job.source_revision != (conv.context_history_revision or 0):
                    job.status, job.partial_summary = "cancelled", None
                    db.commit()
                    continue
                if job.attempts >= 3:
                    job.status, job.error_code = "failed", "summary_retry_exhausted"
                    db.commit()
                    continue
                job.status, job.lease_owner = "running", self.owner
                job.lease_version += 1
                job.lease_expires_at = now + timedelta(seconds=self.LEASE_SECONDS)
                job.attempts += 1
                claim = SimpleNamespace(id=job.id, cid=cid, owner=self.owner, version=job.lease_version)
                db.commit()
                return claim
        return None

    def heartbeat(self, claim):
        with self.sessions() as db:
            job = db.scalar(select(Job).where(Job.id == claim.id).with_for_update())
            if not job or job.status != "running" or not owns_live_lease(job, owner=claim.owner, version=claim.version):
                return False
            job.lease_expires_at = utcnow() + timedelta(seconds=self.LEASE_SECONDS)
            db.commit()
            return True

    def load(self, claim):
        with self.sessions() as db:
            repo = MessageRepository(db)
            conv = repo.lock_conversation(claim.cid)
            job = db.scalar(select(Job).where(Job.id == claim.id).with_for_update())
            if not conv or not self._owns(job, claim) or job.source_revision != (conv.context_history_revision or 0):
                return None
            user = db.get(User, conv.user_id)
            if not user:
                raise ValueError("summary_owner_missing")
            target = db.get(Message, job.target_message_id)
            if not target or target.conversation_id != conv.id:
                raise ValueError("summary_target_missing")
            stmt = select(Message).where(Message.conversation_id == conv.id,
                Message.role.in_(["user", "assistant"]), Message.status != "streaming",
                repo._sql_order_key() <= repo._order_key(target))
            boundary = db.get(Message, job.cursor_message_id) if job.cursor_message_id else None
            if boundary:
                stmt = stmt.where(repo._sql_order_key() > repo._order_key(boundary))
            rows = list(db.scalars(stmt.order_by(*repo.order_columns()).limit(48)))
            if conv.context_summary_boundary_message_id != job.cursor_message_id:
                raise ValueError("summary_cursor_changed")
            runtime = ChatExecutionService(db=db, current_user=user)._build_runtime_config(conv)
            batch = select_summary_batch(rows, tokenizer=runtime.tokenizer,
                max_tokens=summary_source_budget(runtime.budget), partial_id=job.partial_message_id, offset=job.partial_offset)
            source_snapshot = [(r.id, r.content, r.generation_id) for r in rows[:len(batch.rows)]]
            data = SimpleNamespace(runtime=runtime, batch=batch, revision=job.source_revision,
                old_boundary=conv.context_summary_boundary_message_id, old_summary=conv.context_summary,
                existing=job.partial_summary or conv.context_summary, snapshot=source_snapshot)
            data.prompt = build_summary_prompt(existing_summary=data.existing, source_messages=batch.rows,
                                               max_summary_chars=runtime.budget.max_summary_chars)
            data.estimated_input_tokens = runtime.tokenizer.estimate_messages_tokens(data.prompt, image_equiv_tokens=0)
            db.commit()
            return data

    @staticmethod
    def _owns(job, claim):
        return bool(job and job.status == "running" and owns_live_lease(job, owner=claim.owner, version=claim.version))

    def complete(self, claim, data, summary):
        with self.sessions() as db:
            repo = MessageRepository(db)
            conv = repo.lock_conversation(claim.cid)
            job = db.scalar(select(Job).where(Job.id == claim.id).with_for_update())
            if not conv or not self._owns(job, claim) or (conv.context_history_revision or 0) != data.revision:
                return False
            if (conv.context_summary_boundary_message_id, conv.context_summary) != (data.old_boundary, data.old_summary):
                raise ValueError("summary_cursor_changed")
            for mid, content, generation in data.snapshot:
                current = db.execute(select(Message.content, Message.generation_id).where(
                    Message.conversation_id == conv.id, Message.id == mid)).first()
                if current is None or tuple(current) != (content, generation):
                    raise ValueError("summary_source_changed")
            if data.batch.rows:
                job.batches += 1
                job.input_tokens += data.estimated_input_tokens
                job.output_tokens += data.runtime.tokenizer.estimate_text_tokens(summary)
                if data.batch.partial_id:
                    # 分片摘要只在Job内暂存，不能提前宣称整条原文已覆盖。
                    job.partial_message_id, job.partial_offset = data.batch.partial_id, data.batch.offset
                    job.partial_summary = summary
                else:
                    job.partial_message_id, job.partial_offset, job.partial_summary = None, 0, None
                    job.cursor_message_id = data.batch.completed[-1]
                    conv.context_summary, conv.context_summary_boundary_message_id = summary, job.cursor_message_id
                    conv.context_summary_updated_at = utcnow()
                    ConversationSummaryService(db).save_checkpoint(conv)
            job.status = "succeeded" if not data.batch.rows or job.cursor_message_id == job.target_message_id else "queued"
            job.lease_owner, job.lease_expires_at, job.next_attempt_at = None, None, None
            job.attempts, job.error_code = 0, None
            db.commit()
            return True

    def fail(self, claim, *, transient=False, stopped=False):
        with self.sessions() as db:
            job = db.scalar(select(Job).where(Job.id == claim.id).with_for_update())
            if not self._owns(job, claim):
                return
            job.status = "queued" if stopped or (transient and job.attempts < 3) else "failed"
            job.error_code = "summary_worker_stopped" if stopped else "summary_provider_error" if transient else "summary_rebuild_error"
            job.next_attempt_at = utcnow() + timedelta(seconds=(2 ** (job.attempts - 1)) + random.uniform(0, .5))
            if stopped:
                # 部署停机不是Provider失败，不消耗该批次的错误重试额度。
                job.attempts = max(0, job.attempts - 1)
                job.next_attempt_at = None
            job.lease_owner, job.lease_expires_at = None, None
            db.commit()

    async def run_once(self):
        if self.stop.is_set():
            return False
        claim = await asyncio.to_thread(self.claim)
        if not claim:
            return False
        done = asyncio.Event()
        async def execute():
            data = await asyncio.to_thread(self.load, claim)
            if data is None:
                return
            summary = data.existing or ""
            if data.batch.rows:
                runtime = data.runtime
                summary = await asyncio.wait_for(self.provider_factory().complete_chat(
                    provider_type=runtime.provider_type, base_url=runtime.base_url, api_key=runtime.provider_api_key,
                    model_name=runtime.resolved_model, messages=data.prompt,
                    temperature=.2, top_p=.9, max_tokens=min(2048, max(512, runtime.budget.max_summary_chars // 2))),
                    timeout=self.MODEL_TIMEOUT_SECONDS)
                if not summary or not summary.strip():
                    raise ValueError("summary_provider_empty")
                summary = summary.strip()[:runtime.budget.max_summary_chars]
            await asyncio.to_thread(self.complete, claim, data, summary)
        self.active = asyncio.create_task(execute())
        heart = asyncio.create_task(watch_lease(done, interval=self.HEARTBEAT_SECONDS,
            renew=lambda: self.heartbeat(claim), lost=self.active.cancel))
        try:
            await self.active
        except asyncio.CancelledError:
            await asyncio.to_thread(self.fail, claim, stopped=self.stop.is_set(), transient=not self.stop.is_set())
        except Exception as exc:
            # 只按错误类型分类，不将Provider正文或消息内容写入日志。
            status = getattr(exc, "status_code", None)
            transient_types = (TimeoutError, ConnectionError, httpx.TimeoutException, httpx.TransportError, APIConnectionError)
            transient = isinstance(exc, transient_types) or isinstance(exc.__cause__, transient_types) or status in {408, 429, 500, 502, 503, 504}
            await asyncio.to_thread(self.fail, claim, transient=transient)
        finally:
            done.set()
            await heart
            self.active = None
        return True

    async def run_forever(self):
        while not self.stop.is_set():
            if not await self.run_once():
                try:
                    await asyncio.wait_for(self.stop.wait(), timeout=1)
                except asyncio.TimeoutError:
                    pass
