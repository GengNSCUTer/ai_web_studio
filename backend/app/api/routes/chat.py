import asyncio
import base64
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
from app.repositories.message_repo import MessageRepository
from app.repositories.setting_repo import UserSettingRepository
from app.schemas.conversation import ConversationCreate
from app.schemas.message import ChatStreamRequest
from app.services.chat_provider_service import ChatProviderService, resolve_provider_base_url
from app.services.context_governance_service import ContextBudgetPlanner, ContextGovernanceService
from app.services.conversation_service import ConversationService
from app.services.message_service import MessageService
from app.services.setting_service import SettingService

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
    context_summary: str | None


def _build_file_context(message: object) -> str | None:
    attachments = getattr(message, "attachments", []) or []
    chunks: list[str] = []
    for attachment in attachments:
        if getattr(attachment, "kind", None) != "file":
            continue
        parsed_text = (getattr(attachment, "parsed_text", None) or "").strip()
        if not parsed_text:
            continue
        file_name = getattr(attachment, "file_name", "attachment")
        chunks.append(f"[附件文件: {file_name}]\n{parsed_text}")

    if not chunks:
        return None

    return "\n\n".join(chunks).strip()


def _build_history_messages(
    *,
    messages: list,
    system_prompt: str | None,
    context_summary: str | None,
    summary_boundary_message_id: str | None,
    provider_type: str,
) -> list[dict[str, Any]]:
    history: list[dict[str, Any]] = []
    if system_prompt:
        history.append({"role": "system", "content": system_prompt})
    if context_summary:
        history.append(
            {
                "role": "system",
                "content": f"以下是本会话较早历史的压缩摘要，请作为长期上下文参考：\n{context_summary}",
            }
        )

    start_index = 0
    if context_summary and summary_boundary_message_id:
        for index, message in enumerate(messages):
            if getattr(message, "id", None) == summary_boundary_message_id:
                start_index = index + 1
                break

    for message in messages[start_index:]:
        if message.role not in {"user", "assistant", "system"}:
            continue
        if message.role == "assistant" and message.status == "streaming" and not message.content:
            continue
        provider_message = _build_provider_message(message=message, provider_type=provider_type)
        file_context = _build_file_context(message)
        if file_context and provider_message.get("role") == "user":
            provider_message = {
                **provider_message,
                "content": (
                    f"{provider_message.get('content', '')}\n\n"
                    f"以下是本轮附加文件内容，请结合它回答：\n{file_context}"
                ).strip(),
            }
        history.append(provider_message)
    return history


def _stringify_stats(stats: dict[str, Any]) -> str:
    return ";".join(f"{key}={value}" for key, value in stats.items())


def _encode_context_notices(notices: list[str]) -> str:
    if not notices:
        return ""
    payload = json.dumps(notices, ensure_ascii=False).encode("utf-8")
    return base64.b64encode(payload).decode("ascii")


def _load_image_base64(storage_path: str) -> str | None:
    try:
        binary = Path(storage_path).read_bytes()
    except OSError:
        return None

    if not binary:
        return None

    return base64.b64encode(binary).decode("utf-8")


def _build_provider_message(*, message: object, provider_type: str) -> dict[str, Any]:
    role = getattr(message, "role", "user")
    content = getattr(message, "content", "")
    attachments = getattr(message, "attachments", []) or []

    image_payloads: list[tuple[str, str]] = []
    for attachment in attachments:
        if getattr(attachment, "kind", None) != "image":
            continue

        encoded = _load_image_base64(getattr(attachment, "storage_path", ""))
        if not encoded:
            continue
        image_payloads.append((encoded, getattr(attachment, "mime_type", None) or "image/jpeg"))

    if role != "user" or not image_payloads:
        return {"role": role, "content": content}

    if provider_type == "ollama":
        return {
            "role": role,
            "content": content,
            "images": [encoded for encoded, _ in image_payloads],
        }

    if provider_type == "openai-compatible":
        content_parts: list[dict[str, Any]] = []
        for encoded, mime_type in image_payloads:
            content_parts.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{encoded}",
                        "detail": "high",
                    },
                }
            )
        if content.strip():
            content_parts.append({"type": "text", "text": content})
        return {
            "role": role,
            "content": content_parts or [{"type": "text", "text": content}],
        }

    return {"role": role, "content": content}


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


def _prepare_chat_execution(
    *,
    payload: ChatStreamRequest,
    db: Session,
    current_user: User,
) -> ChatExecutionContext:
    conversation_repo = ConversationRepository(db)
    message_service = MessageService(MessageRepository(db), AttachmentRepository(db))
    setting_service = SettingService(UserSettingRepository(db))

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
    governance_service = ContextGovernanceService(budget=budget)
    next_summary, next_summary_boundary_message_id, summary_refresh_stats = governance_service.build_incremental_summary(
        existing_summary=_clean_optional_str(getattr(conversation, "context_summary", None)),
        summary_boundary_message_id=_clean_optional_str(
            getattr(conversation, "context_summary_boundary_message_id", None)
        ),
        conversation_messages=history_rows,
    )
    if summary_refresh_stats["summary_refresh_triggered"] and next_summary:
        conversation.context_summary = next_summary
        conversation.context_summary_boundary_message_id = next_summary_boundary_message_id
        conversation.context_summary_updated_at = datetime.now(timezone.utc)
        conversation_repo.save(conversation)

    history_messages = _build_history_messages(
        messages=history_rows,
        system_prompt=_clean_optional_str(conversation.system_prompt) or _clean_optional_str(default_settings.system_prompt),
        context_summary=next_summary or _clean_optional_str(getattr(conversation, "context_summary", None)),
        summary_boundary_message_id=next_summary_boundary_message_id
        or _clean_optional_str(getattr(conversation, "context_summary_boundary_message_id", None)),
        provider_type=provider_type,
    )
    governed_context = governance_service.govern_messages(history_messages)

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
            "budget_max_attachment_chars": budget.max_attachment_chars,
            "total_chars_estimate": governed_context.stats.total_chars_estimate,
            "truncated_history_messages": governed_context.stats.truncated_history_messages,
            "summary_chars": len(
                next_summary or _clean_optional_str(getattr(conversation, "context_summary", None)) or ""
            ),
            "summary_triggered": int(governed_context.summary_triggered),
            "summary_refresh_triggered": summary_refresh_stats["summary_refresh_triggered"],
            "summary_refresh_source_messages": summary_refresh_stats["summary_refresh_source_messages"],
            "summary_refresh_source_chars": summary_refresh_stats["summary_refresh_source_chars"],
        },
        context_summary=next_summary or _clean_optional_str(getattr(conversation, "context_summary", None)),
    )


@router.post("/text-stream")
async def chat_text_stream(
    payload: ChatStreamRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    provider_service = ChatProviderService()
    context = _prepare_chat_execution(payload=payload, db=db, current_user=current_user)

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
        },
    )
