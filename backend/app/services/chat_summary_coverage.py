"""在任何外部召回或工具执行前，确定本轮可证明覆盖的历史。"""

import logging
from time import perf_counter

from fastapi import HTTPException

from app.repositories.message_repo import MessageRepository
from app.services.chat_execution_models import SummaryRefreshBundle
from app.services.chat_provider_service import ChatProviderService
from app.services.context_governance_service import ContextBudgetExceeded
from app.services.conversation_summary_service import (
    ConversationSummaryService, SummaryRebuildRequired, select_summary_batch, summary_source_budget,
)
from app.services.prompt_builder_service import ContextPromptBuilder
from app.services.runtime_lease import utcnow
from app.services.summary_output import InvalidSummary, generate_summary, merge_summary_facts, render_summary

logger = logging.getLogger(__name__)


def signature(rows):
    return [(row.id, row.role, row.content, row.generation_id) for row in rows]


class ChatSummaryCoverage:
    SYNC_TIMEOUT_SECONDS = 20

    def __init__(self, db):
        self.db = db
        self.summaries = ConversationSummaryService(db)
        self.repo = self.summaries.repo

    def _lock(self, assembly):
        conversation = self.repo.lock_conversation(assembly.conversation_id)
        assistant = self.repo.lock_message(assembly.assistant_id, assembly.conversation_id)
        user = self.repo.message_key(assembly.conversation_id, assembly.user_message_id)
        if (conversation is None or user is None or assistant is None or assistant.status != "streaming"
                or assistant.generation_id != assembly.assistant_generation_id):
            self._changed()
        return conversation, user

    def _changed(self):
        self.db.rollback()
        raise HTTPException(status_code=409, detail="会话历史已变化，请重试本轮回答。")

    def _state(self, conversation):
        return (conversation.context_history_revision, conversation.context_summary_boundary_message_id,
                conversation.context_summary)

    def _base(self, assembly, summary):
        return ContextPromptBuilder().build_chat_messages(
            messages=[assembly.user_message], system_prompt=assembly.system_prompt, memory_context=None,
            context_summary=summary, summary_boundary_message_id=None, external_context=None,
            attachment_context=None, provider_type=assembly.runtime.provider_type,
            skill_instructions=assembly.skill_context.final_answer_instructions if assembly.skill_context else None,
            current_user_payload=assembly.current_user_payload,
        ).messages

    def _suffix(self, assembly, query, summary):
        governance = assembly.runtime.governance_service
        builder = ContextPromptBuilder()
        chars, tokens = governance.require_fit(self._base(assembly, summary))
        selected = []
        while True:
            page = query
            if selected:
                page = page.where(self.repo._sql_order_key() < self.repo._order_key(selected[-1]))
            page = page.order_by(*self.repo.order_columns(descending=True)).limit(24)
            rows = self.db.scalars(page).all()
            for row in rows:
                message = builder.build_provider_message(message=row, provider_type=assembly.runtime.provider_type)
                row_chars, row_tokens = governance.message_cost(message)
                chars += row_chars
                tokens += row_tokens
                if chars > governance.budget.max_total_chars or tokens > governance.budget.max_total_tokens:
                    # 有压力时以模式窗口为目标，当前用户消息单独保留。
                    retained = max(0, governance.budget.max_history_messages - 1)
                    target = selected[retained] if len(selected) > retained else row
                    return None, target
                selected.append(row)
            if len(rows) < 24:
                return MessageRepository.freeze_context_history(reversed(selected)), None

    def _ready(self, assembly, conversation, user, rows, summary, started, *, mode, **stats):
        assembly.history_rows = [*rows, assembly.user_message]
        assembly.history_snapshot = signature(assembly.history_rows)
        assembly.coverage_state = self._state(conversation)
        assembly.coverage_through = self.repo._order_key(user)
        assembly.summary_bundle = SummaryRefreshBundle(summary=summary, boundary_message_id=None, stats={
            "summary_mode": mode, "summary_repair_status": "ready", "history_coverage": "complete",
            "summary_preflight_ms": round((perf_counter() - started) * 1000, 2), **stats,
        })
        self.db.commit()

    def _block(self, conversation, target, started):
        job = self.summaries.ensure_job(conversation, target=target)
        failure = "context_summary_rebuild_failed" if job and job.status == "failed" else "context_summary_rebuilding"
        self.db.commit()
        logger.info("摘要预检阻断 code=%s elapsed_ms=%.1f", failure, (perf_counter() - started) * 1000)
        raise SummaryRebuildRequired(failure)

    def prepare_current_turn(self, assembly):
        _, user = self._lock(assembly)
        assembly.coverage_through = self.repo._order_key(user)
        assembly.history_rows = [assembly.user_message]
        assembly.history_snapshot = signature(assembly.history_rows)
        self.db.commit()

    async def prepare(self, assembly):
        started = perf_counter()
        conversation, user = self._lock(assembly)
        checkpoint = self.summaries.repair_fallback(conversation, self.summaries.checkpoint(conversation))
        old_state = self._state(conversation)
        boundary = self.repo.message_key(conversation.id, old_state[1]) if old_state[1] else None
        if boundary and self.repo._order_key(boundary) >= self.repo._order_key(user):
            self._changed()
        existing = old_state[2] if boundary else None
        if not existing:
            if old_state[1] or old_state[2]:
                conversation.context_summary = conversation.context_summary_boundary_message_id = None
                conversation.context_summary_updated_at = None
                self.db.flush()
                old_state = self._state(conversation)
            boundary = None
        facts = list(checkpoint.facts or []) if checkpoint and boundary else []
        summary = render_summary(existing, facts) or None
        query = self.repo.summary_history(conversation.id, through=user, boundary=boundary, attachments=True)
        rows, target = self._suffix(assembly, query, summary)
        if target is None:
            self._ready(assembly, conversation, user, rows, summary, started,
                        mode=checkpoint.mode if checkpoint and boundary else "none")
            return
        pending = query.where(self.repo._sql_order_key() <= self.repo._order_key(target)).order_by(
            *self.repo.order_columns()).limit(self.repo.SUMMARY_BATCH_LIMIT + 1).execution_options(populate_existing=True)
        if self.summaries.active_job(conversation):
            self._block(conversation, target, started)
        source = self.db.scalars(pending).all()
        runtime = assembly.runtime
        batch = select_summary_batch(source, tokenizer=runtime.tokenizer, max_tokens=summary_source_budget(runtime.budget))
        if len(batch.completed) != len(source):
            self._block(conversation, target, started)
        facts = merge_summary_facts(facts, source)
        target_id = target.id
        frozen_sources = signature(source)
        self.db.commit()
        summary_started = perf_counter()
        try:
            text, used_tokens = await generate_summary(ChatProviderService(), runtime, existing=existing,
                rows=batch.rows, facts=facts, timeout=self.SYNC_TIMEOUT_SECONDS)
        except Exception as exc:
            # 生成失败保留已验证边界，下一轮交给有租约和重试上限的后台任务。
            conversation, _ = self._lock(assembly)
            target = self.repo.message_key(conversation.id, target_id)
            if self._state(conversation) != old_state or target is None:
                self._changed()
            if isinstance(exc, InvalidSummary) and exc.args == ("summary_output_budget_exceeded",):
                facts_only = render_summary(None, facts)
                if (len(facts_only) >= runtime.budget.max_summary_chars
                        or runtime.tokenizer.estimate_text_tokens(facts_only) >= runtime.budget.max_summary_tokens):
                    raise ContextBudgetExceeded() from exc
            self._block(conversation, target, started)
        conversation, user = self._lock(assembly)
        if self._state(conversation) != old_state or signature(self.db.scalars(pending).all()) != frozen_sources:
            self._changed()
        conversation.context_summary, conversation.context_summary_boundary_message_id = text, target_id
        conversation.context_summary_updated_at = utcnow()
        self.summaries.save_checkpoint(conversation, facts=facts)
        self.db.flush()
        boundary = self.repo.message_key(conversation.id, target_id)
        query = self.repo.summary_history(conversation.id, through=user, boundary=boundary, attachments=True)
        summary = render_summary(text, facts)
        rows, target = self._suffix(assembly, query, summary)
        if target is not None:
            self._block(conversation, target, started)
        self._ready(assembly, conversation, user, rows, summary, started, mode="model",
                    summary_refresh_triggered=1, summary_refresh_model_used=1,
                    summary_refresh_source_messages=len(frozen_sources), summary_refresh_source_tokens=batch.tokens,
                    summary_refresh_source_chars=sum(len(row.content or "") for row in batch.rows),
                    summary_refresh_input_tokens=used_tokens,
                    summary_refresh_ms=round((perf_counter() - summary_started) * 1000, 2))

    def verify(self, assembly):
        conversation, user = self._lock(assembly)
        if self.repo._order_key(user) != assembly.coverage_through:
            self._changed()
        user_message = self.db.get(type(assembly.user_message), assembly.user_message_id, populate_existing=True)
        if assembly.history_mode == "current_turn_only":
            if signature([user_message]) != assembly.history_snapshot:
                self._changed()
            return
        if conversation.context_history_revision != assembly.coverage_state[0]:
            self._changed()
        boundary = self.repo.message_key(conversation.id, assembly.coverage_state[1]) if assembly.coverage_state[1] else None
        if assembly.coverage_state[1] and boundary is None:
            self._changed()
        query = self.repo.summary_history(conversation.id, through=user, boundary=boundary)
        current = self.db.scalars(query.order_by(*self.repo.order_columns()).limit(len(assembly.history_rows))
                                 .execution_options(populate_existing=True)).all()
        if signature([*current, user_message]) != assembly.history_snapshot:
            self._changed()
