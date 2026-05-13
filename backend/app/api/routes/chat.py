import asyncio
import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.repositories.attachment_repo import AttachmentRepository
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.memory_repo import UserMemoryRepository
from app.repositories.message_repo import MessageRepository
from app.repositories.setting_repo import UserSettingRepository
from app.schemas.conversation import ConversationCreate
from app.schemas.message import ChatStreamRequest
from app.services.attachment_context_service import AttachmentContextService
from app.services.chat_provider_service import ChatProviderService, resolve_provider_base_url
from app.services.context_governance_service import ContextBudgetPlanner, ContextGovernanceService
from app.services.conversation_service import ConversationService
from app.services.memory_service import MemoryService
from app.services.message_service import MessageService
from app.services.prompt_builder_service import ContextPromptBuilder
from app.services.setting_service import SettingService
from app.services.tokenizer_service import TokenizerEstimator

router = APIRouter(prefix="/chat", tags=["chat"])


@dataclass
class ChatExecutionContext:
    conversation_repo: ConversationRepository
    message_service: MessageService
    conversation: object
    user_message: object
    assistant_message: object
    history_messages: list[dict[str, Any]]
    resolved_model: str
    provider_type: str
    base_url: str
    api_key: str | None
    temperature: float
    top_p: float
    max_tokens: int | None
    context_notices: list[str]
    context_stats: dict[str, Any]
    context_details: dict[str, Any]
    context_summary: str | None


def _stringify_stats(stats: dict[str, Any]) -> str:
    return ";".join(f"{key}={value}" for key, value in stats.items())


def _encode_context_notices(notices: list[str]) -> str:
    if not notices:
        return ""
    payload = json.dumps(notices, ensure_ascii=False).encode("utf-8")
    return base64.b64encode(payload).decode("ascii")


def _encode_json_payload(payload: dict[str, Any] | list[Any] | None) -> str:
    if not payload:
        return ""
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return base64.b64encode(encoded).decode("ascii")


def _is_supported_text_file(attachment: object) -> bool:
    file_name = (getattr(attachment, "file_name", None) or "").strip().lower()
    ext = Path(file_name).suffix.lstrip(".")
    return ext in {"txt", "md", "markdown", "pdf", "docx"}


def _derive_title(title: str | None, content: str) -> str:
    if title:
        return title
    normalized = " ".join(content.strip().split())
    return (normalized[:30] or "New Chat").strip()


def _clean_optional_str(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _build_summary_source_text(source_messages: list[object]) -> str:
    lines: list[str] = []
    for index, message in enumerate(source_messages, start=1):
        role = getattr(message, "role", "unknown")
        content = " ".join((getattr(message, "content", None) or "").split()).strip()
        if not content:
            continue
        lines.append(f"{index}. {role}: {content[:1800]}")
    return "\n".join(lines).strip()


def _build_summary_prompt(
    *,
    existing_summary: str | None,
    source_messages: list[object],
    max_summary_chars: int,
) -> list[dict[str, str]]:
    source_text = _build_summary_source_text(source_messages)
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


async def _prepare_chat_execution(
    *,
    payload: ChatStreamRequest,
    db: Session,
    current_user: User,
) -> ChatExecutionContext:
    conversation_repo = ConversationRepository(db)
    message_service = MessageService(MessageRepository(db), AttachmentRepository(db))
    setting_service = SettingService(UserSettingRepository(db))
    memory_service = MemoryService(UserMemoryRepository(db))

    default_settings = setting_service.get_or_create_user_settings(current_user.id)

    conversation = None
    if payload.conversation_id:
        conversation = conversation_repo.get_by_user(payload.conversation_id, current_user.id)
        if not conversation:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    if not conversation:
        conversation_response = ConversationService(conversation_repo).create_conversation(
            ConversationCreate(
                title=_derive_title(payload.title, payload.content),
                model_name=_clean_optional_str(payload.model_name) or _clean_optional_str(default_settings.default_model),
                system_prompt=_clean_optional_str(payload.system_prompt) or _clean_optional_str(default_settings.system_prompt),
            ),
            current_user.id,
        )
        conversation = conversation_repo.get_by_user(conversation_response.id, current_user.id)
        if not conversation:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Conversation bootstrap failed",
            )

    if conversation.title == "New Chat" and payload.content.strip():
        conversation.title = _derive_title(payload.title, payload.content)
        conversation_repo.save(conversation)

    cleaned_system_prompt = _clean_optional_str(payload.system_prompt)
    cleaned_model_name = _clean_optional_str(payload.model_name)

    if payload.system_prompt is not None:
        conversation.system_prompt = cleaned_system_prompt
        conversation_repo.save(conversation)

    if cleaned_model_name is not None and cleaned_model_name != conversation.model_name:
        conversation.model_name = cleaned_model_name
        conversation_repo.save(conversation)

    unsupported_attachments = [
        item
        for item in payload.attachments
        if item.kind == "file" and not _is_supported_text_file(item)
    ]
    if unsupported_attachments:
        unsupported_names = "、".join(item.file_name for item in unsupported_attachments)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"当前仅支持 txt、md、pdf 文档进入上下文，暂不支持：{unsupported_names}",
        )
    attachments_missing_text = [
        item
        for item in payload.attachments
        if item.kind == "file" and not (item.parsed_text or "").strip()
    ]
    if attachments_missing_text:
        missing_names = "、".join(item.file_name for item in attachments_missing_text)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"以下文档未解析到有效文本，暂时无法进入上下文：{missing_names}",
        )

    user_message = message_service.create_system_message(
        conversation_id=conversation.id,
        role="user",
        content=payload.content,
        status="done",
    )
    attachments = message_service.attach_uploaded_items(
        message_id=user_message.id,
        uploads=payload.attachments,
        user_id=current_user.id,
    )
    if attachments:
        user_message.attachments = attachments
    history_rows = MessageRepository(db).list_by_conversation(conversation.id)
    assistant_message = message_service.create_system_message(
        conversation_id=conversation.id,
        role="assistant",
        content="",
        status="streaming",
    )
    conversation_repo.touch(conversation.id)

    resolved_model = _clean_optional_str(conversation.model_name) or _clean_optional_str(default_settings.default_model)
    provider_type = _clean_optional_str(getattr(default_settings, "provider_type", "ollama")) or "ollama"
    base_url = resolve_provider_base_url(
        provider_type=provider_type,
        configured_base_url=_clean_optional_str(default_settings.ollama_base_url),
    )
    budget = ContextBudgetPlanner.build(
        model_context_window=max(8192, int(getattr(default_settings, "model_context_window", 128000) or 128000)),
        context_mode=_clean_optional_str(getattr(default_settings, "context_mode", "balanced")) or "balanced",
    )
    tokenizer = TokenizerEstimator(model_name=resolved_model)
    governance_service = ContextGovernanceService(budget=budget, tokenizer=tokenizer)
    memory_context = None
    memory_count = 0
    memory_chars = 0
    if getattr(default_settings, "memory_enabled", True):
        memory_context, memory_count, memory_chars = memory_service.build_memory_context(
            current_user.id,
            max_chars=int(getattr(default_settings, "memory_max_chars", 4000) or 4000),
        )
    attachment_context_result = AttachmentContextService().build_context(
        attachments=list(getattr(user_message, "attachments", []) or []),
        query=payload.content,
        max_chars=budget.max_attachment_chars,
    )

    async def summarize_with_model(
        *,
        existing_summary: str | None,
        source_messages: list[object],
        max_summary_chars: int,
    ) -> str | None:
        summary_model = resolved_model or _clean_optional_str(default_settings.default_model)
        if not summary_model:
            return None
        summary = await ChatProviderService().complete_chat(
            provider_type=provider_type,
            base_url=base_url,
            api_key=_clean_optional_str(getattr(default_settings, "api_key", None)),
            model_name=summary_model,
            messages=_build_summary_prompt(
                existing_summary=existing_summary,
                source_messages=source_messages,
                max_summary_chars=max_summary_chars,
            ),
            temperature=0.2,
            top_p=0.9,
            max_tokens=min(2048, max(512, max_summary_chars // 2)),
        )
        return summary[:max_summary_chars].strip() if summary else None

    next_summary, next_summary_boundary_message_id, summary_refresh_stats = await governance_service.build_incremental_summary(
        existing_summary=_clean_optional_str(getattr(conversation, "context_summary", None)),
        summary_boundary_message_id=_clean_optional_str(
            getattr(conversation, "context_summary_boundary_message_id", None)
        ),
        conversation_messages=history_rows,
        summarizer=summarize_with_model,
    )
    if summary_refresh_stats["summary_refresh_triggered"] and next_summary:
        conversation.context_summary = next_summary
        conversation.context_summary_boundary_message_id = next_summary_boundary_message_id
        conversation.context_summary_updated_at = datetime.now(timezone.utc)
        conversation_repo.save(conversation)

    prompt_result = ContextPromptBuilder().build_chat_messages(
        messages=history_rows,
        system_prompt=_clean_optional_str(conversation.system_prompt) or _clean_optional_str(default_settings.system_prompt),
        memory_context=memory_context,
        context_summary=next_summary or _clean_optional_str(getattr(conversation, "context_summary", None)),
        summary_boundary_message_id=next_summary_boundary_message_id
        or _clean_optional_str(getattr(conversation, "context_summary_boundary_message_id", None)),
        attachment_context=attachment_context_result.context_text,
        provider_type=provider_type,
        model_name=resolved_model,
    )
    governed_context = governance_service.govern_messages(prompt_result.messages)
    stable_prefix_text = json.dumps(
        prompt_result.stable_prefix_messages,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    prompt_prefix_hash = hashlib.sha256(stable_prefix_text.encode("utf-8")).hexdigest()[:16] if stable_prefix_text else ""
    prompt_prefix_tokens = tokenizer.estimate_messages_tokens(
        prompt_result.stable_prefix_messages,
        image_equiv_tokens=budget.max_image_equiv_tokens,
    )
    prompt_total_tokens = tokenizer.estimate_messages_tokens(
        governed_context.messages,
        image_equiv_tokens=budget.max_image_equiv_tokens,
    )
    prompt_recent_history_tokens = max(0, prompt_total_tokens - prompt_prefix_tokens)
    previous_prompt_prefix_hash = _clean_optional_str(getattr(conversation, "last_prompt_prefix_hash", None))
    prompt_prefix_reused_last_turn = int(bool(prompt_prefix_hash and prompt_prefix_hash == previous_prompt_prefix_hash))

    conversation.last_prompt_prefix_hash = prompt_prefix_hash or None
    conversation.last_prompt_prefix_token_count = prompt_prefix_tokens or None
    conversation_repo.save(conversation)

    summary_text = next_summary or _clean_optional_str(getattr(conversation, "context_summary", None)) or ""
    summary_tokens = tokenizer.estimate_text_tokens(summary_text)
    summary_source_tokens = int(summary_refresh_stats.get("summary_refresh_source_tokens", 0) or 0)
    summary_compression_ratio = (
        round(summary_tokens / summary_source_tokens, 4)
        if summary_source_tokens > 0
        else 0
    )
    attachment_context_tokens = tokenizer.estimate_text_tokens(attachment_context_result.context_text or "")
    context_details = {
        "attachment_chunks": attachment_context_result.details.get("attachment_chunks", []),
    }

    return ChatExecutionContext(
        conversation_repo=conversation_repo,
        message_service=message_service,
        conversation=conversation,
        user_message=user_message,
        assistant_message=assistant_message,
        history_messages=governed_context.messages,
        resolved_model=resolved_model,
        provider_type=provider_type,
        base_url=base_url,
        api_key=_clean_optional_str(getattr(default_settings, "api_key", None)),
        temperature=default_settings.temperature,
        top_p=default_settings.top_p,
        max_tokens=default_settings.max_tokens,
        context_notices=governed_context.notices,
        context_stats={
            "context_mode": budget.context_mode,
            "model_context_window": budget.model_context_window,
            "budget_max_total_chars": budget.max_total_chars,
            "budget_max_total_tokens": budget.max_total_tokens,
            "budget_max_attachment_chars": budget.max_attachment_chars,
            "budget_max_attachment_tokens": budget.max_attachment_tokens,
            "total_chars_estimate": governed_context.stats.total_chars_estimate,
            "total_tokens_estimate": governed_context.stats.total_tokens_estimate,
            "truncated_history_messages": governed_context.stats.truncated_history_messages,
            "summary_chars": len(
                next_summary or _clean_optional_str(getattr(conversation, "context_summary", None)) or ""
            ),
            "summary_tokens": summary_tokens,
            "summary_triggered": int(governed_context.summary_triggered),
            "summary_refresh_triggered": summary_refresh_stats["summary_refresh_triggered"],
            "summary_refresh_source_messages": summary_refresh_stats["summary_refresh_source_messages"],
            "summary_refresh_source_chars": summary_refresh_stats["summary_refresh_source_chars"],
            "summary_refresh_model_used": summary_refresh_stats["summary_refresh_model_used"],
            "summary_refresh_fallback_used": summary_refresh_stats["summary_refresh_fallback_used"],
            "summary_refresh_source_tokens": summary_source_tokens,
            "summary_compression_ratio": summary_compression_ratio,
            "attachment_context_tokens": attachment_context_tokens,
            "memory_enabled": int(bool(getattr(default_settings, "memory_enabled", True))),
            "memory_injected": int(bool(memory_context)),
            "memory_count": memory_count,
            "memory_chars": memory_chars,
            "tokenizer_encoding": tokenizer.estimate.encoding_name,
            "prompt_prefix_hash": prompt_prefix_hash,
            "prompt_prefix_tokens": prompt_prefix_tokens,
            "prompt_total_tokens": prompt_total_tokens,
            "prompt_recent_history_tokens": prompt_recent_history_tokens,
            "prompt_prefix_reused_last_turn": prompt_prefix_reused_last_turn,
            **attachment_context_result.diagnostics,
            **prompt_result.diagnostics,
        },
        context_details=context_details,
        context_summary=next_summary or _clean_optional_str(getattr(conversation, "context_summary", None)),
    )


@router.post("/text-stream")
async def chat_text_stream(
    payload: ChatStreamRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    provider_service = ChatProviderService()
    context = await _prepare_chat_execution(payload=payload, db=db, current_user=current_user)

    async def text_generator():
        content_parts: list[str] = []
        try:
            async for chunk in provider_service.stream_chat(
                provider_type=context.provider_type,
                base_url=context.base_url,
                api_key=context.api_key,
                model_name=context.resolved_model,
                messages=context.history_messages,
                temperature=context.temperature,
                top_p=context.top_p,
                max_tokens=context.max_tokens,
            ):
                content_parts.append(chunk)
                yield chunk

            context.assistant_message.content = "".join(content_parts)
            context.assistant_message.status = "done"
            context.message_service.save_message(context.assistant_message)
            context.conversation_repo.touch(context.conversation.id)
        except asyncio.CancelledError:
            context.assistant_message.status = "cancelled"
            context.assistant_message.content = "".join(content_parts)
            context.message_service.save_message(context.assistant_message)
            context.conversation_repo.touch(context.conversation.id)
            raise
        except Exception:
            context.assistant_message.status = "failed"
            context.assistant_message.content = "".join(content_parts)
            context.message_service.save_message(context.assistant_message)
            context.conversation_repo.touch(context.conversation.id)
            raise

    return StreamingResponse(
        text_generator(),
        media_type="text/plain; charset=utf-8",
        headers={
            "cache-control": "no-cache, no-transform",
            "x-conversation-id": context.conversation.id,
            "x-assistant-message-id": context.assistant_message.id,
            "x-context-notices": _encode_context_notices(context.context_notices),
            "x-context-stats": _stringify_stats(context.context_stats),
            "x-context-details": _encode_json_payload(context.context_details),
        },
    )
