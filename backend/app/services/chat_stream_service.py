"""共享Chat生成事件服务；Worker直接消费，HTTP订阅不拥有生成任务。"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import aclosing
from typing import Any

import anyio
from sqlalchemy import select

from app.core.config import settings
from app.models.observability import ChatRuntimeMetric
from app.services.chat_execution_service import (
    ChatExecutionContext,
)
from app.services.chat_provider_service import ChatProviderService, ChatStreamEvent
from app.services.chat_persistence_service import ChatPersistenceSnapshot, persist_stream_result
from app.services.citation_audit_service import audit_answer_citations
from app.services.agent_graph.chat_stream_graph import ChatStreamGraphContext, stream_chat_graph

logger = logging.getLogger(__name__)


class ChatStreamTimeoutError(TimeoutError):
    """Chat Provider 流式迭代超过某一层时间预算。"""

    def __init__(self, timeout_kind: str) -> None:
        self.timeout_kind = timeout_kind
        super().__init__(timeout_kind)


class ChatOutputLimitError(RuntimeError):
    """Provider明确报告输出额度耗尽，部分回答不能当作完整完成。"""


def _public_stream_error(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, ChatOutputLimitError):
        return "output_token_limit", "模型达到本次输出Token上限，已保留部分回答，请缩小回答范围后重试。"
    if isinstance(exc, ChatStreamTimeoutError):
        messages = {
            "first_token_timeout": "模型在规定时间内未开始响应，请稍后重试。",
            "stream_idle_timeout": "模型流式输出中断过久，请稍后重试。",
            "stream_total_timeout": "模型回答超过最大允许时长，已停止生成。",
        }
        return exc.timeout_kind, messages.get(exc.timeout_kind, "模型调用超时，请稍后重试。")
    # 第三方异常可能包含 URL、响应正文甚至鉴权信息，只在服务端日志记录原始异常。
    return "provider_error", "模型调用失败，请稍后重试。"


async def _iter_with_stream_timeouts(
    source: AsyncIterator[Any],
    *,
    first_token_timeout_seconds: float,
    idle_timeout_seconds: float,
    total_timeout_seconds: float,
) -> AsyncIterator[Any]:
    """为 Provider 事件流分别施加首事件、闲置和总时长限制。"""
    timeout_values = {
        "first_token_timeout": first_token_timeout_seconds,
        "stream_idle_timeout": idle_timeout_seconds,
        "stream_total_timeout": total_timeout_seconds,
    }
    invalid_names = [name for name, value in timeout_values.items() if value <= 0]
    if invalid_names:
        raise ValueError(f"Chat stream timeout must be positive: {', '.join(invalid_names)}")

    loop = asyncio.get_running_loop()
    started_at = loop.time()
    first_event = True
    iterator = source.__aiter__()
    try:
        while True:
            remaining_total = total_timeout_seconds - (loop.time() - started_at)
            if remaining_total <= 0:
                raise ChatStreamTimeoutError("stream_total_timeout")

            phase_timeout = first_token_timeout_seconds if first_event else idle_timeout_seconds
            wait_timeout = min(phase_timeout, remaining_total)
            try:
                event = await asyncio.wait_for(iterator.__anext__(), timeout=wait_timeout)
            except StopAsyncIteration:
                return
            except asyncio.TimeoutError as exc:
                timeout_kind = (
                    "stream_total_timeout"
                    if remaining_total <= phase_timeout
                    else "first_token_timeout" if first_event else "stream_idle_timeout"
                )
                raise ChatStreamTimeoutError(timeout_kind) from exc

            first_event = False
            yield event
    finally:
        close = getattr(iterator, "aclose", None)
        if close is not None:
            await close()


def _truncate_diagnostic_text(value: Any, max_chars: int) -> Any:
    if not isinstance(value, str):
        return value
    if len(value) <= max_chars:
        return value
    return value[:max_chars].rstrip() + "...[truncated]"


def _summarize_context_details(details: dict[str, Any]) -> dict[str, Any]:
    """为context_info保留有限诊断摘要，完整来源与工具轨迹通过独立事件发送。"""
    compact: dict[str, Any] = {}
    memory = details.get("memory_retrieval")
    if isinstance(memory, dict):
        compact["memory_retrieval"] = memory

    attachment_chunks = details.get("attachment_chunks")
    if isinstance(attachment_chunks, list):
        compact["attachment_chunks"] = [
            {
                **chunk,
                "preview": _truncate_diagnostic_text(chunk.get("preview"), 360),
                "expanded_preview": _truncate_diagnostic_text(chunk.get("expanded_preview"), 720),
            }
            for chunk in attachment_chunks[:8]
            if isinstance(chunk, dict)
        ]

    external_sources = details.get("external_sources")
    if isinstance(external_sources, list):
        compact["external_sources"] = [
            {
                "source_type": source.get("source_type"),
                "provider": source.get("provider"),
                "title": _truncate_diagnostic_text(source.get("title"), 80),
                "display_text": _truncate_diagnostic_text(source.get("display_text"), 120),
                "url": _truncate_diagnostic_text(source.get("url"), 120),
                "rank": source.get("rank"),
                "score": source.get("score"),
                "citation_label": source.get("citation_label"),
                "metadata": {
                    key: _truncate_diagnostic_text(value, 80)
                    for key, value in (source.get("metadata") or {}).items()
                    if key in {"tool", "query", "domain", "city", "province", "name", "address", "type"}
                },
            }
            for source in external_sources[:3]
            if isinstance(source, dict)
        ]

    tool_plan = details.get("tool_plan")
    if isinstance(tool_plan, dict):
        compact["tool_plan"] = {
            "router": tool_plan.get("router"),
            "should_use_tools": tool_plan.get("should_use_tools"),
            "need_more_rounds": tool_plan.get("need_more_rounds"),
            "calls": [
                {
                    "tool_key": call.get("tool_key"),
                    "display_name": call.get("display_name"),
                    "confidence": call.get("confidence"),
                }
                for call in (tool_plan.get("calls") or [])[:5]
                if isinstance(call, dict)
            ],
        }

    active_skill = details.get("active_skill")
    if isinstance(active_skill, dict):
        compact["active_skill"] = {
            "skill_key": active_skill.get("skill_key"),
            "version": active_skill.get("version"),
            "display_name": _truncate_diagnostic_text(active_skill.get("display_name"), 80),
            "allowed_tool_keys": [
                str(tool_key)[:128]
                for tool_key in (active_skill.get("allowed_tool_keys") or [])[:16]
            ],
        }

    evidence_sufficiency = details.get("evidence_sufficiency")
    if isinstance(evidence_sufficiency, dict):
        compact["evidence_sufficiency"] = {
            "status": evidence_sufficiency.get("status"),
            "required_source_types": evidence_sufficiency.get("required_source_types", []),
            "available_source_types": evidence_sufficiency.get("available_source_types", []),
            "reasons": evidence_sufficiency.get("reasons", []),
        }

    # 运行模式和预算是前端诊断面板需要的安全摘要；不携带参数、URL 或 Provider 原文。
    for key in ("tool_run_policy", "tool_run_budget"):
        value = details.get(key)
        if isinstance(value, dict):
            compact[key] = value

    return compact


def _stream_event(event_type: str, **payload: Any) -> dict:
    # 进程内传递业务对象；仅HTTP订阅边界编码为SSE。
    return {"type": event_type, **payload}


def _persist_stream_result(
    context: ChatExecutionContext,
    *,
    status_value: str,
    content_parts: list[str],
    reasoning_parts: list[str],
) -> bool:
    """统一收口流式结果，避免完成、取消和失败分支写出不同的消息字段。"""
    content = "".join(content_parts)
    audit = _stream_citation_audit(context, content=content, status_value=status_value)
    reasoning_content = "".join(reasoning_parts) or None
    external_sources = (
        json.dumps(context.external_sources, ensure_ascii=False) if context.external_sources else None
    )
    persisted = context.message_service.save_generation_result(
        message=context.assistant_message,
        generation_id=getattr(context, "generation_id", None),
        content=content,
        reasoning_content=reasoning_content,
        external_sources=external_sources,
        status=status_value,
        citation_audit=json.dumps(audit, ensure_ascii=False),
    )
    if not persisted:
        # 另一个请求已经换代并接管了同一条 assistant。旧请求可以结束，
        # 但不能再写消息、指标或自动记忆任务。
        return False
    # Observability is a separate best-effort record. The assistant message is
    # already committed above; metric persistence must never change chat outcome.
    try:
        metric_db = context.message_service.repo.db
        metric = metric_db.scalars(
            select(ChatRuntimeMetric)
            .where(ChatRuntimeMetric.assistant_message_id == context.assistant_message.id)
            .limit(1)
        ).first()
        if not metric:
            metric = ChatRuntimeMetric(
                user_id=context.conversation.user_id,
                conversation_id=context.conversation.id,
                assistant_message_id=context.assistant_message.id,
                provider_type=context.provider_type,
                model_name=context.resolved_model,
                status=status_value,
            )
            metric_db.add(metric)
        metric.provider_type = context.provider_type
        metric.model_name = context.resolved_model
        metric.status = status_value
        metric.stats_json = json.dumps(context.context_stats, ensure_ascii=False, default=str)
        metric_db.commit()
    except Exception:
        context.message_service.repo.db.rollback()
        logger.warning("Failed to persist chat runtime metrics")
    if status_value == "done":
        # 自动记忆只落一个持久化 extraction job；真正的 LLM 提取由独立 worker
        # 完成并生成 pending 候选，不延长当前 HTTP 流，也不会直接激活记忆。
        try:
            from app.services.memory_candidate_runtime import MemoryExtractionJobService

            MemoryExtractionJobService(context.message_service.repo.db).enqueue_after_turn(
                user_id=context.conversation.user_id,
                conversation_id=context.conversation.id,
                assistant_message_id=context.assistant_message.id,
            )
        except Exception:
            context.message_service.repo.db.rollback()
            logger.warning("Failed to enqueue memory candidate extraction job")
    return True


def _stream_citation_audit(context, *, content: str, status_value: str) -> dict:
    """每次终态都记录引用质量，不修改原文或把标签问题混成传输失败。"""
    audit = audit_answer_citations(content, sources=context.external_sources, generation_status=status_value,
        smalltalk=context.context_details.get("citation_smalltalk", False),
        known_labels=context.context_details.get("known_source_labels", ()))
    context.context_details["citation_audit"] = audit
    context.context_stats.update({"citation_audit_status": audit["status"],
        "citation_unknown_count": len(audit["unknown_labels"]), "citation_excluded_count": len(audit["excluded_labels"]),
        "citation_missing": int(audit["missing_citation"])})
    return audit


async def _persist_stream_result_nonblocking(
    context: ChatExecutionContext,
    *,
    status_value: str,
    content_parts: list[str],
    reasoning_parts: list[str],
    assistant_message_id: str,
    conversation_id: str,
    user_id: str,
    postgresql: bool,
) -> bool:
    """在流式事件循环外收口 PostgreSQL 写入，保留 SQLite 测试兼容路径。"""

    if not postgresql:
        # 内存 SQLite 测试数据库没有可复用的全局异步连接串，继续使用测试会话。
        return _persist_stream_result(
            context,
            status_value=status_value,
            content_parts=content_parts,
            reasoning_parts=reasoning_parts,
        )

    audit = _stream_citation_audit(context, content="".join(content_parts), status_value=status_value)
    snapshot = ChatPersistenceSnapshot(
        assistant_message_id=assistant_message_id,
        conversation_id=conversation_id,
        user_id=user_id,
        generation_id=getattr(context, "generation_id", None),
        provider_type=context.provider_type,
        model_name=context.resolved_model,
        status=status_value,
        content="".join(content_parts),
        reasoning_content="".join(reasoning_parts) or None,
        external_sources=(
            json.dumps(context.external_sources, ensure_ascii=False) if context.external_sources else None
        ),
        context_stats=context.context_stats,
        citation_audit=json.dumps(audit, ensure_ascii=False),
    )
    return await persist_stream_result(snapshot)


def generate_chat_events(
    context: ChatExecutionContext,
    provider_service: ChatProviderService,
    *,
    persist_callback=None,
) -> AsyncIterator[dict]:
    """统一生成、超时、审计和收口，只返回业务事件，不创建HTTP响应。

    正式Web路径只订阅Worker已经持久化的事件；本函数由Worker或诊断脚本调用。
    persist_callback让ChatRun在自己的租约保护事务中同时保存消息与任务终态。
    """

    # 先把 ORM 对象身份冻结为普通字符串。后续模型流可能持续数分钟，不能让
    # 请求 Session 因一次 ORM 属性刷新在 PostgreSQL 中保持 idle-in-transaction。
    db = context.message_service.repo.db
    postgresql = getattr(getattr(db.bind, "dialect", None), "name", "") == "postgresql"
    conversation_id = str(context.conversation.id)
    user_id = str(context.conversation.user_id)
    assistant_message_id = str(context.assistant_message.id)
    if getattr(context, 'verified_file_edit_answer', None) is not None:
        context.context_stats['answer_origin'] = 'verified_file_edit_status'

    async def text_generator():
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        stream_context = None
        async def save(status_value):
            if persist_callback is not None:
                return await persist_callback(status_value, content_parts, reasoning_parts,
                    stream_context.error if stream_context else None)
            return await _persist_stream_result_nonblocking(
                context, status_value=status_value, content_parts=content_parts,
                reasoning_parts=reasoning_parts, assistant_message_id=assistant_message_id,
                conversation_id=conversation_id, user_id=user_id, postgresql=postgresql)
        try:
            # 诊断通过业务事件发送，最终由订阅边界编码为SSE。
            yield _stream_event("context_info", conversation_id=conversation_id, info={
                "notices": context.context_notices,
                "stats": {key: str(value) for key, value in context.context_stats.items() if key != "memory_retrieval"},
                "details": _summarize_context_details(context.context_details),
            })
            # 工具事件先发给前端，让用户看到“为什么调用工具、调用了什么、是否成功”。
            for tool_event in context.tool_events:
                event_type = str(tool_event.get("type") or "")
                payload = {key: value for key, value in tool_event.items() if key != "type"}
                if event_type:
                    yield _stream_event(event_type, **payload)
            if context.tool_events or context.external_sources:
                yield _stream_event(
                    "tool_sources",
                    sources=context.external_sources,
                )
            async def verified_edit_events():
                yield ChatStreamEvent(type='answer_delta', text=context.verified_file_edit_answer)

            if getattr(context, 'verified_file_edit_answer', None) is not None:
                # 状态来源是本轮实际记录；不调用最终模型，沿用正常流式持久化和generation校验。
                provider_events = verified_edit_events()
            else:
                provider_events = provider_service.stream_chat_events(
                    provider_type=context.provider_type,
                    base_url=context.base_url,
                    api_key=context.api_key,
                    model_name=context.resolved_model,
                    messages=context.history_messages,
                    temperature=context.temperature,
                    top_p=context.top_p,
                    max_tokens=context.max_tokens,
                    thinking_enabled=context.thinking_enabled,
                    thinking_budget=context.thinking_budget,
                    prompt_cache_key=context.prompt_cache_key,
                    prompt_cache_breakpoint=context.prompt_cache_breakpoint,
                )
            async def persist_terminal(status_value: str) -> bool:
                return await save(status_value)

            stream_context = ChatStreamGraphContext(
                source=_iter_with_stream_timeouts(
                    provider_events,
                    first_token_timeout_seconds=settings.chat_first_token_timeout_seconds,
                    idle_timeout_seconds=settings.chat_stream_idle_timeout_seconds,
                    total_timeout_seconds=settings.chat_stream_total_timeout_seconds,
                ),
                persist=persist_terminal, content_parts=content_parts, reasoning_parts=reasoning_parts,
                context_details=context.context_details, context_stats=context.context_stats,
            )
            async with aclosing(stream_chat_graph(stream_context)) as graph_events:
                async for event in graph_events:
                    if event.type == "provider_finish" and event.data:
                        reason = str(event.data.get("finish_reason") or "unknown")
                        context.context_stats["provider_finish_reason"] = reason
                        yield _stream_event("provider_finish", finish_reason=reason)
                        if reason == "length":
                            raise ChatOutputLimitError()
                        continue
                    if event.type == "reasoning_delta":
                        # reasoning_delta 是深度思考过程；单独存储，避免混入最终回答正文。
                        yield _stream_event("reasoning_delta", text=event.text)
                        continue
                    if event.type == "answer_delta":
                        # answer_delta 才是最终回答正文。
                        yield _stream_event("answer_delta", text=event.text)
                        continue
                    if event.type == "provider_usage" and event.data:
                        # usage 事件来自 Provider 最终 chunk，不包含凭据；它让前端/测试能够
                        # 区分“理论前缀可复用”和“Provider 实际命中 cached tokens”。
                        context.context_stats.update(
                            {
                                "provider_input_tokens": int(event.data.get("input_tokens", 0) or 0),
                                "provider_output_tokens": int(event.data.get("output_tokens", 0) or 0),
                                "provider_cached_input_tokens": int(event.data.get("cached_input_tokens", 0) or 0),
                                "provider_cache_creation_input_tokens": int(
                                    event.data.get("cache_creation_input_tokens", 0) or 0
                                ),
                                "prompt_cache_mode": event.data.get("prompt_cache_mode", "none"),
                                "prompt_cache_request_key_sent": int(
                                    bool(event.data.get("prompt_cache_request_key_sent", False))
                                ),
                                "prompt_cache_usage_available": int(
                                    bool(event.data.get("prompt_cache_usage_available", False))
                                ),
                                "prompt_cache_usage_support": event.data.get(
                                    "prompt_cache_usage_support", "none"
                                ),
                            }
                        )
                        yield _stream_event("provider_usage", **event.data)

            # 图生成节点结束后由独立节点保存；路由只按保存结果输出终态事件。
            if stream_context.error is not None:
                raise stream_context.error
            persisted = stream_context.persisted
            if not persisted:
                yield _stream_event(
                    "model_error",
                    error="该回答已被新的生成请求接管。",
                    error_code="generation_superseded",
                    assistant_message_id=assistant_message_id,
                )
                return
            yield _stream_event("citation_audit", audit=context.context_details["citation_audit"])
            yield _stream_event("done", assistant_message_id=assistant_message_id)
        except asyncio.CancelledError:
            # 生成任务被取消时保存部分内容；浏览器断开只影响SSE订阅，不取消Worker生成器。
            # 短时间屏蔽外部取消以完成数据库收口，避免部分回答永久留在streaming。
            with anyio.move_on_after(10, shield=True) as save_scope:
                try:
                    await save("cancelled")
                except Exception:
                    logger.warning("Failed to persist cancelled chat stream")
            if save_scope.cancel_called:
                logger.warning("Timed out while persisting cancelled chat stream")
            raise
        except Exception as exc:
            # 模型错误也要保存 partial content/reasoning/sources，否则刷新后会丢失已生成片段和诊断线索。
            # 不记录原始异常文本：第三方 SDK 可能把 URL、响应正文或凭据片段放进异常消息。
            if isinstance(exc, ChatStreamTimeoutError):
                logger.warning("Chat provider stream timed out: %s", exc.timeout_kind)
            else:
                logger.error("Chat provider stream failed: %s", type(exc).__name__)
            if stream_context is not None and stream_context.error is None:
                stream_context.error = exc
            # 生成错误已由图终态节点保存，避免重复写入和重复派发后台记忆任务。
            if stream_context is not None and stream_context.persisted is not None:
                persisted = stream_context.persisted
            else:
                persisted = await save("failed")
            if not persisted:
                yield _stream_event(
                    "model_error",
                    error="该回答已被新的生成请求接管。",
                    error_code="generation_superseded",
                    assistant_message_id=assistant_message_id,
                )
                return
            error_code, public_error = _public_stream_error(exc)
            yield _stream_event("citation_audit", audit=context.context_details["citation_audit"])
            yield _stream_event(
                "model_error",
                error=public_error,
                error_code=error_code,
                assistant_message_id=assistant_message_id,
            )
            return

    if postgresql:
        db.close()
    return text_generator()
