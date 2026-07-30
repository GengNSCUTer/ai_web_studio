from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from app.repositories.conversation_repo import ConversationRepository
from app.repositories.tool_trace_repo import ToolTraceRepository
from app.repositories.knowledge_repo import KnowledgeRetrievalLogRepository
from app.services.attachment_context_service import AttachmentContextService
from app.services.chat_execution_models import (
    ChatExecutionContext,
    ChatRuntimeConfig,
    MemoryContextBundle,
    PromptDiagnosticsBundle,
    SummaryRefreshBundle,
)
from app.services.chat_provider_service import ChatProviderService
from app.services.external_context_service import ExternalContextService
from app.services.knowledge_context_service import KnowledgeContextService
from app.services.message_service import MessageService
from app.services.prompt_builder_service import ContextPromptBuilder
from app.services.tools.planner import PlannerRuntime


def clean_optional_str(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def build_summary_source_text(source_messages: list[object]) -> str:
    # 摘要模型不需要完整原文；这里先把候选历史压成 role + 前 1800 字，控制摘要成本。
    lines: list[str] = []
    for index, message in enumerate(source_messages, start=1):
        role = getattr(message, "role", "unknown")
        content = " ".join((getattr(message, "content", None) or "").split()).strip()
        if not content:
            continue
        lines.append(f"{index}. {role}: {content[:1800]}")
    return "\n".join(lines).strip()


def build_summary_prompt(
    *,
    existing_summary: str | None,
    source_messages: list[object],
    max_summary_chars: int,
) -> list[dict[str, str]]:
    # 摘要本质是“压缩历史”，不是回答用户问题；system prompt 明确禁止新增事实。
    source_text = build_summary_source_text(source_messages)
    existing = (existing_summary or "").strip()
    target_chars = max(800, min(max_summary_chars, 6000))

    system_prompt = (
        "你是一个对话上下文压缩器。你的任务是把较早历史压缩成后续问答可用的滚动摘要，"
        "不要回答用户问题，不要新增不存在的信息。"
    )
    user_prompt = f"""请基于已有摘要和新增历史，生成一份可继续用于后续对话的中文滚动摘要。

要求：
- 保留用户目标、偏好、明确约束、重要事实、已经做过的决定、待办事项。
- 删除寒暄、重复表达、无关过程和低价值细节。
- 如果已有摘要与新增历史冲突，以新增历史为准，并在摘要中体现最新状态。
- 输出使用简洁 Markdown，最多约 {target_chars} 个中文字符。
- 只输出摘要正文，不要输出解释。

【已有滚动摘要】
{existing or "无"}

【新增历史】
{source_text or "无"}
"""
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


class ChatContextAssemblyService:
    """上下文组装层。

    这一层不创建消息，也不调用最终回答模型。它负责把所有上下文来源合并成模型可消费的 messages：
    长期记忆、当前轮附件、联网/工具结果、知识库片段、滚动摘要、最近历史、上下文预算治理。
    """

    def __init__(
        self,
        *,
        db: object,
        user_id: str,
        conversation_repo: ConversationRepository,
        message_service: MessageService,
        tool_trace_repo: ToolTraceRepository,
        memory_service: object,
    ) -> None:
        self.db = db
        self.user_id = user_id
        self.conversation_repo = conversation_repo
        self.message_service = message_service
        self.tool_trace_repo = tool_trace_repo
        self.memory_service = memory_service

    def build_memory_context(self, settings: object, *, query: str | None = None) -> MemoryContextBundle:
        # 长期记忆是用户级上下文，不属于单个 conversation；是否注入由用户设置控制。
        if not getattr(settings, "memory_enabled", True):
            return MemoryContextBundle(context_text=None, count=0, chars=0)

        context_text, count, chars = self.memory_service.build_memory_context(
            self.user_id,
            max_chars=int(getattr(settings, "memory_max_chars", 4000) or 4000),
            query=query,
        )
        return MemoryContextBundle(context_text=context_text, count=count, chars=chars)

    async def build_execution_context(
        self,
        *,
        conversation: object,
        history_rows: list[object],
        user_message: object,
        assistant_message: object,
        runtime: ChatRuntimeConfig,
        thinking_enabled: bool,
        thinking_budget: int | None,
        web_search_enabled: bool,
        knowledge_base_id: str | None = None,
        knowledge_base_ids: list[str] | None = None,
    ) -> ChatExecutionContext:
        # 这是 Chat prepare 阶段的核心方法：收集所有上下文来源，构造最终 prompt，并返回给流式执行层。
        query = getattr(user_message, "content", "") or ""
        memory_bundle = self.build_memory_context(runtime.settings, query=query)

        # 当前轮附件上下文只围绕本轮 user_message 选择，不扫描全部历史附件。
        attachment_context_result = AttachmentContextService().build_context(
            attachments=list(getattr(user_message, "attachments", []) or []),
            query=query,
            max_chars=runtime.budget.max_attachment_chars,
        )
        # 外部上下文包括联网搜索、地图、天气等工具。只有 web_search_enabled=True 时才会真正规划/调用工具。
        external_context_result = await self._build_external_context(
            conversation=conversation,
            assistant_message=assistant_message,
            user_message=user_message,
            query=query,
            history_rows=history_rows,
            web_search_enabled=web_search_enabled,
            max_attachment_chars=runtime.budget.max_attachment_chars,
            runtime=runtime,
        )
        # 知识库上下文来自用户显式选择的知识库；检索日志会在后面绑定到本轮 user/assistant message。
        knowledge_context_result = await KnowledgeContextService(
            db=self.db,
            user_id=self.user_id,
        ).build_context(
            knowledge_base_id=knowledge_base_id,
            knowledge_base_ids=knowledge_base_ids,
            query=query,
            recent_messages=[
                message
                for message in history_rows
                if message is not user_message
                and (
                    not getattr(user_message, "id", None)
                    or getattr(message, "id", None) != getattr(user_message, "id", None)
                )
            ],
        )
        retrieval_log_ids = knowledge_context_result.retrieval_log_ids or (
            [knowledge_context_result.retrieval_log_id] if knowledge_context_result.retrieval_log_id else []
        )
        if retrieval_log_ids:
            # RAG 来源定位需要把检索日志绑定到本轮消息，否则前端只能看到片段，不能跳回对应回答。
            knowledge_log_repo = KnowledgeRetrievalLogRepository(self.db)
            sources_by_log_id: dict[str, list[dict[str, Any]]] = {}
            for source in knowledge_context_result.sources:
                source_dict = source.to_public_dict()
                log_id = str((source.metadata or {}).get("retrieval_log_id") or "")
                if log_id:
                    sources_by_log_id.setdefault(log_id, []).append(source_dict)
            for log_id in retrieval_log_ids:
                knowledge_log_repo.update_message_links(
                    log_id=log_id,
                    user_id=self.user_id,
                    conversation_id=getattr(conversation, "id", None),
                    user_message_id=getattr(user_message, "id", None),
                    assistant_message_id=getattr(assistant_message, "id", None),
                    sources=sources_by_log_id.get(log_id, []),
                )
        summary_bundle = await self._refresh_context_summary(
            conversation=conversation,
            history_rows=history_rows,
            runtime=runtime,
        )

        # PromptBuilder 负责把各层上下文转成 provider chat messages；Governance 再按预算做截断。
        prompt_result = ContextPromptBuilder().build_chat_messages(
            messages=history_rows,
            system_prompt=clean_optional_str(conversation.system_prompt)
            or clean_optional_str(getattr(runtime.settings, "system_prompt", None)),
            memory_context=memory_bundle.context_text,
            context_summary=summary_bundle.summary or clean_optional_str(getattr(conversation, "context_summary", None)),
            summary_boundary_message_id=summary_bundle.boundary_message_id
            or clean_optional_str(getattr(conversation, "context_summary_boundary_message_id", None)),
            external_context=external_context_result.context_text,
            attachment_context=attachment_context_result.context_text,
            knowledge_context=knowledge_context_result.context_text,
            provider_type=runtime.provider_type,
            model_name=runtime.resolved_model,
        )
        governed_context = runtime.governance_service.govern_messages(prompt_result.messages)
        prompt_diagnostics = self._build_prompt_diagnostics(
            conversation=conversation,
            prompt_result=prompt_result,
            governed_messages=governed_context.messages,
            runtime=runtime,
        )

        conversation.last_prompt_prefix_hash = prompt_diagnostics.prompt_prefix_hash or None
        conversation.last_prompt_prefix_token_count = prompt_diagnostics.prompt_prefix_tokens or None
        # prefix hash 只是观测/缓存命中诊断字段，不影响业务权限。
        self.conversation_repo.save(conversation)
        # ConversationRepository 只 flush；摘要边界与 prefix 诊断在 Context Assembly 用例末尾统一提交。
        self.db.commit()

        summary_text = summary_bundle.summary or clean_optional_str(getattr(conversation, "context_summary", None)) or ""
        summary_tokens = runtime.tokenizer.estimate_text_tokens(summary_text)
        summary_source_tokens = int(summary_bundle.stats.get("summary_refresh_source_tokens", 0) or 0)
        summary_compression_ratio = (
            round(summary_tokens / summary_source_tokens, 4) if summary_source_tokens > 0 else 0
        )
        attachment_context_tokens = runtime.tokenizer.estimate_text_tokens(attachment_context_result.context_text or "")
        knowledge_context_tokens = runtime.tokenizer.estimate_text_tokens(knowledge_context_result.context_text or "")
        combined_sources = [*external_context_result.sources, *knowledge_context_result.sources]
        combined_public_sources = [source.to_public_dict() for source in combined_sources]
        context_details = {
            "attachment_chunks": attachment_context_result.details.get("attachment_chunks", []),
            "external_sources": combined_public_sources,
            "knowledge_sources": knowledge_context_result.details.get("knowledge_sources", []),
            "knowledge_query_rewrite": knowledge_context_result.details.get("knowledge_query_rewrite"),
            "tool_plan": external_context_result.details.get("tool_plan"),
            "tool_events": external_context_result.details.get("tool_events", []),
        }

        return ChatExecutionContext(
            conversation_repo=self.conversation_repo,
            message_service=self.message_service,
            conversation=conversation,
            user_message=user_message,
            assistant_message=assistant_message,
            history_messages=governed_context.messages,
            resolved_model=runtime.resolved_model,
            provider_type=runtime.provider_type,
            base_url=runtime.base_url,
            api_key=runtime.provider_api_key,
            temperature=runtime.settings.temperature,
            top_p=runtime.settings.top_p,
            # Provider 输出上限必须与预算预留一致，否则输入合规后仍可能在生成阶段挤爆窗口。
            max_tokens=runtime.budget.reserved_output_tokens,
            context_notices=[
                *external_context_result.notices,
                *knowledge_context_result.notices,
                *governed_context.notices,
            ],
            context_stats={
                "context_mode": runtime.budget.context_mode,
                "model_context_window": runtime.budget.model_context_window,
                "budget_reserved_output_tokens": runtime.budget.reserved_output_tokens,
                "budget_max_total_chars": runtime.budget.max_total_chars,
                "budget_max_total_tokens": runtime.budget.max_total_tokens,
                "budget_max_attachment_chars": runtime.budget.max_attachment_chars,
                "budget_max_attachment_tokens": runtime.budget.max_attachment_tokens,
                "total_chars_estimate": governed_context.stats.total_chars_estimate,
                "total_tokens_estimate": governed_context.stats.total_tokens_estimate,
                "truncated_history_messages": governed_context.stats.truncated_history_messages,
                "summary_chars": len(
                    summary_bundle.summary or clean_optional_str(getattr(conversation, "context_summary", None)) or ""
                ),
                "summary_tokens": summary_tokens,
                "summary_triggered": int(governed_context.summary_triggered),
                "summary_refresh_triggered": summary_bundle.stats["summary_refresh_triggered"],
                "summary_refresh_source_messages": summary_bundle.stats["summary_refresh_source_messages"],
                "summary_refresh_source_chars": summary_bundle.stats["summary_refresh_source_chars"],
                "summary_refresh_model_used": summary_bundle.stats["summary_refresh_model_used"],
                "summary_refresh_fallback_used": summary_bundle.stats["summary_refresh_fallback_used"],
                "summary_boundary_reset": summary_bundle.stats["summary_boundary_reset"],
                "summary_refresh_source_tokens": summary_source_tokens,
                "summary_compression_ratio": summary_compression_ratio,
                "attachment_context_tokens": attachment_context_tokens,
                "knowledge_context_tokens": knowledge_context_tokens,
                "memory_enabled": int(bool(getattr(runtime.settings, "memory_enabled", True))),
                "memory_injected": int(bool(memory_bundle.context_text)),
                "memory_count": memory_bundle.count,
                "memory_chars": memory_bundle.chars,
                "thinking_enabled": int(bool(thinking_enabled)),
                "tokenizer_encoding": runtime.tokenizer.estimate.encoding_name,
                "prompt_prefix_hash": prompt_diagnostics.prompt_prefix_hash,
                "prompt_prefix_tokens": prompt_diagnostics.prompt_prefix_tokens,
                "prompt_total_tokens": prompt_diagnostics.prompt_total_tokens,
                "prompt_recent_history_tokens": prompt_diagnostics.prompt_recent_history_tokens,
                "prompt_prefix_reused_last_turn": prompt_diagnostics.prompt_prefix_reused_last_turn,
                **attachment_context_result.diagnostics,
                **external_context_result.diagnostics,
                **knowledge_context_result.diagnostics,
                **prompt_result.diagnostics,
            },
            context_details=context_details,
            context_summary=summary_bundle.summary or clean_optional_str(getattr(conversation, "context_summary", None)),
            thinking_enabled=thinking_enabled,
            thinking_budget=thinking_budget,
            tool_events=[event.to_public_dict() for event in external_context_result.tool_events],
            external_sources=combined_public_sources,
        )

    async def _build_external_context(
        self,
        *,
        conversation: object,
        assistant_message: object,
        user_message: object,
        query: str,
        history_rows: list[object],
        web_search_enabled: bool,
        max_attachment_chars: int,
        runtime: ChatRuntimeConfig,
    ) -> object:
        # 工具层的输入不只是当前 query，还包含 recent_messages 和 planner_runtime。
        # 这让 LLM planner 可以根据上下文判断是否需要多工具调用，而不是纯正则匹配。
        external_context_result = await ExternalContextService(
            db=self.db,
            user_id=self.user_id,
            project_id=getattr(conversation, "project_id", None),
        ).build_context(
            query=query,
            enabled=web_search_enabled,
            max_chars=max(1200, min(max_attachment_chars, 6000)),
            recent_messages=list(history_rows),
            planner_runtime=PlannerRuntime(
                provider_type=runtime.provider_type,
                base_url=runtime.base_url,
                api_key=runtime.provider_api_key,
                model_name=runtime.resolved_model,
            ),
        )
        self.tool_trace_repo.replace_for_assistant_message(
            user_id=self.user_id,
            conversation_id=conversation.id,
            user_message_id=getattr(user_message, "id", None),
            assistant_message_id=getattr(assistant_message, "id"),
            query=query,
            external_context=external_context_result,
        )
        return external_context_result

    async def _refresh_context_summary(
        self,
        *,
        conversation: object,
        history_rows: list[object],
        runtime: ChatRuntimeConfig,
    ) -> SummaryRefreshBundle:
        # 滚动摘要只在上下文治理判断需要时刷新；刷新失败时治理层会走 fallback，不应阻断普通聊天。
        async def summarize_with_model(
            *,
            existing_summary: str | None,
            source_messages: list[object],
            max_summary_chars: int,
        ) -> str | None:
            summary_model = runtime.resolved_model or clean_optional_str(getattr(runtime.settings, "default_model", None))
            if not summary_model:
                return None
            summary = await ChatProviderService().complete_chat(
                provider_type=runtime.provider_type,
                base_url=runtime.base_url,
                api_key=runtime.provider_api_key,
                model_name=summary_model,
                messages=build_summary_prompt(
                    existing_summary=existing_summary,
                    source_messages=source_messages,
                    max_summary_chars=max_summary_chars,
                ),
                temperature=0.2,
                top_p=0.9,
                max_tokens=min(2048, max(512, max_summary_chars // 2)),
            )
            return summary[:max_summary_chars].strip() if summary else None

        next_summary, boundary_message_id, stats = await runtime.governance_service.build_incremental_summary(
            existing_summary=clean_optional_str(getattr(conversation, "context_summary", None)),
            summary_boundary_message_id=clean_optional_str(
                getattr(conversation, "context_summary_boundary_message_id", None)
            ),
            conversation_messages=history_rows,
            summarizer=summarize_with_model,
        )
        if stats["summary_boundary_reset"]:
            # boundary 消失意味着旧摘要覆盖范围不可验证；没有足够旧消息生成新摘要时也必须清空旧状态。
            conversation.context_summary = next_summary
            conversation.context_summary_boundary_message_id = boundary_message_id
            conversation.context_summary_updated_at = datetime.now(timezone.utc) if next_summary else None
            self.conversation_repo.save(conversation)
        elif stats["summary_refresh_triggered"] and next_summary:
            # 摘要和边界必须一起保存，否则后续 prompt 无法知道哪些历史已被摘要覆盖。
            conversation.context_summary = next_summary
            conversation.context_summary_boundary_message_id = boundary_message_id
            conversation.context_summary_updated_at = datetime.now(timezone.utc)
            self.conversation_repo.save(conversation)
        return SummaryRefreshBundle(summary=next_summary, boundary_message_id=boundary_message_id, stats=stats)

    @staticmethod
    def _build_prompt_diagnostics(
        *,
        conversation: object,
        prompt_result: object,
        governed_messages: list[dict[str, Any]],
        runtime: ChatRuntimeConfig,
    ) -> PromptDiagnosticsBundle:
        # stable_prefix_messages 只包含系统层上下文，不包含最近用户历史。
        # hash 相同表示 prefix 可能复用，有利于观察 prompt cache 命中潜力。
        stable_prefix_text = json.dumps(
            prompt_result.stable_prefix_messages,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        prompt_prefix_hash = (
            hashlib.sha256(stable_prefix_text.encode("utf-8")).hexdigest()[:16]
            if stable_prefix_text
            else ""
        )
        prompt_prefix_tokens = runtime.tokenizer.estimate_messages_tokens(
            prompt_result.stable_prefix_messages,
            image_equiv_tokens=runtime.budget.max_image_equiv_tokens,
        )
        prompt_total_tokens = runtime.tokenizer.estimate_messages_tokens(
            governed_messages,
            image_equiv_tokens=runtime.budget.max_image_equiv_tokens,
        )
        prompt_recent_history_tokens = max(0, prompt_total_tokens - prompt_prefix_tokens)
        previous_prompt_prefix_hash = clean_optional_str(getattr(conversation, "last_prompt_prefix_hash", None))
        prompt_prefix_reused_last_turn = int(
            bool(prompt_prefix_hash and prompt_prefix_hash == previous_prompt_prefix_hash)
        )
        return PromptDiagnosticsBundle(
            prompt_prefix_hash=prompt_prefix_hash,
            prompt_prefix_tokens=prompt_prefix_tokens,
            prompt_total_tokens=prompt_total_tokens,
            prompt_recent_history_tokens=prompt_recent_history_tokens,
            prompt_prefix_reused_last_turn=prompt_prefix_reused_last_turn,
        )
