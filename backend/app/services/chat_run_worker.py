"""独立Chat Worker：浏览器只读事件，生成、心跳和收口由本进程拥有。"""

import asyncio
from contextlib import aclosing
import json
import logging
import os
import socket
import time
from uuid import uuid4

from app.core.config import settings
from app.models.chat_run import ChatRun
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.user import User
from app.services.chat_execution_models import ExistingTurnExecutionInput
from app.services.chat_execution_service import ChatExecutionService
from app.services.chat_provider_service import ChatProviderService
from app.services.chat_run_guard import current_chat_guard
from app.services.chat_run_service import ChatLeaseLost, ChatRunService
from app.services.chat_tool_runtime import ChatToolPersistence, current_tool_persistence
from app.services.durable_worker_registry import DurableWorkerRegistry
from app.services.runtime_lease import aware, utcnow, watch_lease

logger = logging.getLogger(__name__)


class ChatRunWorker:
    HEARTBEAT_SECONDS = 1

    def __init__(self, *, session_factory, owner=None, provider_factory=ChatProviderService):
        self.sessions = session_factory
        self.owner = owner or f"chat-worker:{socket.gethostname()}:{os.getpid()}:{uuid4().hex[:8]}"
        self.provider_factory = provider_factory
        self.stop = asyncio.Event()
        self.active = set()

    def request_stop(self):
        self.stop.set()
        for task in list(self.active):
            task.cancel()

    def _call(self, method, *args, **kwargs):
        with self.sessions() as db:
            return getattr(ChatRunService(db), method)(*args, **kwargs)

    def _guard(self, claim):
        with self.sessions() as db:
            run = ChatRunService(db)._locked_claim(claim)
            if run is None or (run.deadline_at and aware(run.deadline_at) <= utcnow()):
                raise ChatLeaseLost()
            db.rollback()

    async def run_once(self):
        if self.stop.is_set():
            return False
        claim = await asyncio.to_thread(self._call, "claim_next", self.owner)
        if not claim:
            return False
        if self.stop.is_set():
            await asyncio.to_thread(self._call, "interrupt", claim, error_code="worker_stopped_before_start")
            return True
        partial = {"content": None, "reasoning": None}
        task = asyncio.create_task(self._execute(claim, partial))
        self.active.add(task)
        heartbeat_stop = asyncio.Event()
        heartbeat = asyncio.create_task(watch_lease(heartbeat_stop, interval=self.HEARTBEAT_SECONDS,
            renew=lambda: self._call("heartbeat", claim), lost=task.cancel))
        try:
            await asyncio.wait_for(task, timeout=settings.chat_run_total_seconds)
        except asyncio.TimeoutError:
            await asyncio.to_thread(self._call, "finish", claim, status="expired", error_code="run_total_timeout",
                content=partial["content"], reasoning=partial["reasoning"])
        except asyncio.CancelledError:
            # 主动取消优先；有恢复记录的停机可重新排队，不把旧半截正文拼到新回答。
            await asyncio.to_thread(self._call, "interrupt", claim, error_code="worker_interrupted",
                content=partial["content"], reasoning=partial["reasoning"])
        except Exception as exc:
            code = "output_budget_exhausted" if isinstance(exc, ValueError) else "prepare_or_worker_error"
            logger.warning("ChatRun执行失败，异常类型：%s", type(exc).__name__)
            await asyncio.to_thread(self._call, "finish", claim, status="failed", error_code=code,
                content=partial["content"], reasoning=partial["reasoning"])
        finally:
            heartbeat_stop.set()
            await heartbeat
            self.active.discard(task)
        return True

    async def _execute(self, claim, partial):
        from app.services.chat_stream_service import generate_chat_events, _stream_citation_audit, _public_stream_error

        token = current_chat_guard.set(lambda: self._guard(claim))
        persistence = ChatToolPersistence(self.sessions, claim)
        persistence_token = current_tool_persistence.set(persistence)
        try:
            await asyncio.to_thread(persistence.initialize)
            with self.sessions() as db:
                run = db.get(ChatRun, claim.run_id)
                if not run:
                    return
                request = json.loads(run.request_json)["payload"]
                user = db.get(User, run.user_id)
                conversation = db.get(Conversation, run.conversation_id)
                user_message = db.get(Message, run.user_message_id)
                assistant = db.get(Message, run.assistant_message_id)
                if not user or not conversation or not user_message or not assistant:
                    raise RuntimeError("Chat任务源已删除")
                if assistant.generation_id != run.generation_id:
                    raise ChatLeaseLost()
                service = ChatExecutionService(db=db, current_user=user, manage_prepare_failure=False)
                history = service.message_repo.list_for_context(conversation, exclude_message_id=assistant.id,
                    through_message_id=user_message.id)
                options = {name: request.get(name) for name in (
                    "model_name", "system_prompt", "thinking_enabled", "thinking_budget", "web_search_enabled",
                    "knowledge_base_id", "knowledge_base_ids", "skill_key", "tool_run_mode")}
                output_tokens = run.max_output_tokens
                context = await service.prepare_existing_turn_execution(ExistingTurnExecutionInput(
                    conversation=conversation, history_rows=history, user_message=user_message,
                    assistant_message=assistant, **options))
                context.max_tokens = min(context.max_tokens or output_tokens, output_tokens)
                context.context_stats.update(chat_run_id=claim.run_id, chat_run_lease_version=claim.version,
                                             chat_run_output_token_limit=context.max_tokens,
                                             chat_tool_run_id=persistence.agent_id)
                if not await asyncio.to_thread(self._call, "start_generation", claim,
                    uses_model=context.verified_file_edit_answer is None):
                    raise ChatLeaseLost()
                pending = []
                last_flush = time.monotonic()
                flush_lock = asyncio.Lock()

                async def flush():
                    nonlocal last_flush
                    async with flush_lock:
                        if pending:
                            batch = list(pending)
                            pending.clear()
                            if not await asyncio.to_thread(self._call, "append", claim, batch):
                                raise ChatLeaseLost()
                        last_flush = time.monotonic()

                async def persist(status_value, content_parts, reasoning_parts, error):
                    partial["content"], partial["reasoning"] = "".join(content_parts), "".join(reasoning_parts)
                    if status_value == "cancelled":
                        # 由外层按取消/超时/停机原因统一收口；此处只保留尚未批量提交的片段。
                        return False
                    await flush()
                    audit = _stream_citation_audit(context, content=partial["content"], status_value=status_value)
                    code = _public_stream_error(error)[0] if error else None
                    saved = await asyncio.to_thread(self._call, "finish", claim,
                        status="succeeded" if status_value == "done" else "failed",
                        content=partial["content"], reasoning=partial["reasoning"], sources=context.external_sources,
                        audit=audit, error_code=code, metric={"provider_type": context.provider_type,
                            "model_name": context.resolved_model, "stats": context.context_stats})
                    if saved and status_value == "done":
                        await asyncio.to_thread(self._enqueue_memory, claim.run_id)
                    return saved

                events = generate_chat_events(context, self.provider_factory(), persist_callback=persist)
                # 这里只消费生成图的事件迭代器，不创建HTTP响应或等待订阅者。
                async with aclosing(events) as chunks:
                    next_chunk = None
                    try:
                        while True:
                            if next_chunk is None:
                                next_chunk = asyncio.create_task(chunks.__anext__())
                            done, _ = await asyncio.wait({next_chunk}, timeout=.15)
                            if not done:
                                # 模型暂时没有下一片段时，也按时保存已经收到的短片段。
                                await flush()
                                continue
                            try:
                                chunk = next_chunk.result()
                            except StopAsyncIteration:
                                break
                            next_chunk = None
                            event = json.loads(chunk)
                            if event["type"] in {"done", "model_error", "citation_audit"}:
                                continue
                            if pending and event["type"] in {"answer_delta", "reasoning_delta"} and pending[-1]["type"] == event["type"]:
                                pending[-1]["text"] += event["text"]
                            else:
                                pending.append(event)
                            if time.monotonic() - last_flush >= .15 or sum(len(str(e.get("text", ""))) for e in pending) >= 512 or event["type"] not in {"answer_delta", "reasoning_delta"}:
                                await flush()
                    finally:
                        if next_chunk is not None:
                            next_chunk.cancel()
                            await asyncio.gather(next_chunk, return_exceptions=True)
        finally:
            current_tool_persistence.reset(persistence_token)
            current_chat_guard.reset(token)

    def _enqueue_memory(self, run_id):
        from app.services.memory_candidate_runtime import MemoryExtractionJobService
        try:
            with self.sessions() as db:
                run = db.get(ChatRun, run_id)
                if run and run.status == "succeeded":
                    MemoryExtractionJobService(db).enqueue_after_turn(user_id=run.user_id,
                        conversation_id=run.conversation_id, assistant_message_id=run.assistant_message_id)
        except Exception:
            logger.warning("ChatRun完成后的记忆任务暂未登记")

    async def run_forever(self):
        instance = await asyncio.to_thread(self._registry, "register", owner=self.owner)
        tasks = set()
        last_prune = 0
        try:
            while not self.stop.is_set():
                registered = await asyncio.to_thread(self._registry, "heartbeat", owner=self.owner, instance_id=instance,
                    status="running" if tasks else "idle")
                if not registered:
                    raise RuntimeError("ChatWorker已失去注册身份")
                while len(tasks) < settings.chat_worker_concurrency and not self.stop.is_set():
                    tasks.add(asyncio.create_task(self.run_once()))
                await asyncio.sleep(.5)
                # 取出已完成任务异常，避免循环任务静默失败。
                for task in list(tasks):
                    if task.done():
                        try:
                            task.result()
                        except Exception:
                            logger.warning("ChatWorker轮询失败，稍后重试")
                        tasks.discard(task)
                if time.monotonic() - last_prune > 60:
                    await asyncio.to_thread(self._call, "reconcile")
                    await asyncio.to_thread(self._call, "prune_events")
                    last_prune = time.monotonic()
        finally:
            self.request_stop()
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.to_thread(self._registry, "stop", owner=self.owner, instance_id=instance)

    def _registry(self, method, **values):
        with self.sessions() as db:
            return getattr(DurableWorkerRegistry(db), method)(**values)
