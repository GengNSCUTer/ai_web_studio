import asyncio
import base64
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.repositories.attachment_repo import AttachmentRepository
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.message_repo import MessageRepository
from app.schemas.message import ChatEditLastUserRequest, ChatRegenerateRequest, ChatStreamRequest
from app.services.chat_execution_service import (
    ChatExecutionContext,
    ChatExecutionService,
    ExistingTurnExecutionInput,
)
from app.services.chat_provider_service import ChatProviderService
from app.services.message_service import MessageService

router = APIRouter(prefix="/chat", tags=["chat"])


def _stringify_stats(stats: dict[str, Any]) -> str:
    return ";".join(f"{key}={value}" for key, value in stats.items())[:2048]


def _encode_context_notices(notices: list[str]) -> str:
    if not notices:
        return ""
    payload = json.dumps(notices[:8], ensure_ascii=False).encode("utf-8")
    return base64.b64encode(payload).decode("ascii")


def _encode_json_payload(payload: dict[str, Any] | list[Any] | None, *, max_encoded_chars: int = 4096) -> str:
    if not payload:
        return ""
    encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    value = base64.b64encode(encoded).decode("ascii")
    return value if len(value) <= max_encoded_chars else ""


def _truncate_header_text(value: Any, max_chars: int) -> Any:
    if not isinstance(value, str):
        return value
    if len(value) <= max_chars:
        return value
    return value[:max_chars].rstrip() + "...[truncated]"


def _compact_context_details_for_header(details: dict[str, Any]) -> dict[str, Any]:
    """Keep diagnostic headers small; full sources are streamed in NDJSON body."""
    compact: dict[str, Any] = {}

    attachment_chunks = details.get("attachment_chunks")
    if isinstance(attachment_chunks, list):
        compact["attachment_chunks"] = [
            {
                **chunk,
                "preview": _truncate_header_text(chunk.get("preview"), 360),
                "expanded_preview": _truncate_header_text(chunk.get("expanded_preview"), 720),
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
                "title": _truncate_header_text(source.get("title"), 80),
                "display_text": _truncate_header_text(source.get("display_text"), 120),
                "url": _truncate_header_text(source.get("url"), 120),
                "rank": source.get("rank"),
                "score": source.get("score"),
                "citation_label": source.get("citation_label"),
                "metadata": {
                    key: _truncate_header_text(value, 80)
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

    return compact


def _encode_stream_event(event_type: str, **payload: Any) -> str:
    return json.dumps({"type": event_type, **payload}, ensure_ascii=False) + "\n"


def _find_latest_user_before(messages: list[object], *, assistant_index: int) -> object | None:
    for message in reversed(messages[:assistant_index]):
        if getattr(message, "role", None) == "user":
            return message
    return None


def _build_streaming_response(
    context: ChatExecutionContext,
    provider_service: ChatProviderService,
    *,
    event_stream: bool = False,
) -> StreamingResponse:
    async def text_generator():
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        try:
            if event_stream:
                for tool_event in context.tool_events:
                    event_type = str(tool_event.get("type") or "")
                    payload = {key: value for key, value in tool_event.items() if key != "type"}
                    if event_type:
                        yield _encode_stream_event(event_type, **payload)
                yield _encode_stream_event(
                    "tool_sources",
                    sources=context.external_sources,
                )
            async for event in provider_service.stream_chat_events(
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
            ):
                if event.type == "reasoning_delta":
                    reasoning_parts.append(event.text)
                    if event_stream:
                        yield _encode_stream_event("reasoning_delta", text=event.text)
                    continue
                if event.type == "answer_delta":
                    content_parts.append(event.text)
                    yield _encode_stream_event("answer_delta", text=event.text) if event_stream else event.text

            context.assistant_message.content = "".join(content_parts)
            context.assistant_message.reasoning_content = "".join(reasoning_parts) or None
            context.assistant_message.external_sources = (
                json.dumps(context.external_sources, ensure_ascii=False) if context.external_sources else None
            )
            context.assistant_message.status = "done"
            context.message_service.save_message(context.assistant_message)
            context.conversation_repo.touch(context.conversation.id)
            if event_stream:
                yield _encode_stream_event("done", assistant_message_id=context.assistant_message.id)
        except asyncio.CancelledError:
            context.assistant_message.status = "cancelled"
            context.assistant_message.content = "".join(content_parts)
            context.assistant_message.reasoning_content = "".join(reasoning_parts) or None
            context.message_service.save_message(context.assistant_message)
            context.conversation_repo.touch(context.conversation.id)
            raise
        except Exception as exc:
            context.assistant_message.status = "failed"
            context.assistant_message.content = "".join(content_parts)
            context.assistant_message.reasoning_content = "".join(reasoning_parts) or None
            context.message_service.save_message(context.assistant_message)
            context.conversation_repo.touch(context.conversation.id)
            if event_stream:
                yield _encode_stream_event(
                    "model_error",
                    error=str(exc) or "模型调用失败",
                    assistant_message_id=context.assistant_message.id,
                )
                return
            raise

    return StreamingResponse(
        text_generator(),
        media_type="application/x-ndjson; charset=utf-8" if event_stream else "text/plain; charset=utf-8",
        headers={
            "cache-control": "no-cache, no-transform",
            "x-conversation-id": context.conversation.id,
            "x-assistant-message-id": context.assistant_message.id,
            "x-context-notices": _encode_context_notices(context.context_notices),
            "x-context-stats": _stringify_stats(context.context_stats),
            "x-context-details": _encode_json_payload(
                _compact_context_details_for_header(context.context_details)
            ),
        },
    )


@router.post("/text-stream")
async def chat_text_stream(
    payload: ChatStreamRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    provider_service = ChatProviderService()
    context = await ChatExecutionService(db=db, current_user=current_user).prepare_chat_execution(payload)
    return _build_streaming_response(context, provider_service)


@router.post("/events-stream")
async def chat_events_stream(
    payload: ChatStreamRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    provider_service = ChatProviderService()
    context = await ChatExecutionService(db=db, current_user=current_user).prepare_chat_execution(payload)
    return _build_streaming_response(context, provider_service, event_stream=True)


@router.post("/regenerate-last-stream")
async def regenerate_last_answer_stream(
    payload: ChatRegenerateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    provider_service = ChatProviderService()
    conversation_repo = ConversationRepository(db)
    message_repo = MessageRepository(db)

    conversation = conversation_repo.get_by_user(payload.conversation_id, current_user.id)
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    history_rows = message_repo.list_by_conversation(conversation.id)
    if not history_rows:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="当前会话还没有可重生成的回答")

    assistant_index = len(history_rows) - 1
    assistant_message = history_rows[assistant_index]
    if (
        getattr(assistant_message, "role", None) != "assistant"
        or getattr(assistant_message, "id", None) != payload.assistant_message_id
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="当前仅支持重生成最后一条回答")

    user_message = _find_latest_user_before(history_rows, assistant_index=assistant_index)
    if not user_message:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="未找到对应的上一条用户消息")

    assistant_message.content = ""
    assistant_message.status = "streaming"
    message_repo.save(assistant_message)

    context = await ChatExecutionService(db=db, current_user=current_user).prepare_existing_turn_execution(
        ExistingTurnExecutionInput(
            conversation=conversation,
            history_rows=history_rows[:-1],
            user_message=user_message,
            assistant_message=assistant_message,
            model_name=payload.model_name,
            system_prompt=payload.system_prompt,
            thinking_enabled=payload.thinking_enabled,
            thinking_budget=payload.thinking_budget,
            web_search_enabled=payload.web_search_enabled,
        )
    )
    return _build_streaming_response(context, provider_service, event_stream=True)


@router.post("/edit-last-user-stream")
async def edit_last_user_stream(
    payload: ChatEditLastUserRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> StreamingResponse:
    provider_service = ChatProviderService()
    conversation_repo = ConversationRepository(db)
    message_repo = MessageRepository(db)
    attachment_repo = AttachmentRepository(db)
    message_service = MessageService(message_repo, attachment_repo)

    conversation = conversation_repo.get_by_user(payload.conversation_id, current_user.id)
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="消息内容不能为空")

    history_rows = message_repo.list_by_conversation(conversation.id)
    if len(history_rows) < 2:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="当前会话还没有可编辑重答的最后一轮")

    assistant_index = len(history_rows) - 1
    assistant_message = history_rows[assistant_index]
    user_message = _find_latest_user_before(history_rows, assistant_index=assistant_index)
    if (
        getattr(assistant_message, "role", None) != "assistant"
        or getattr(assistant_message, "id", None) != payload.assistant_message_id
        or not user_message
        or getattr(user_message, "id", None) != payload.user_message_id
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="当前仅支持编辑最后一条用户消息并重新回答")

    user_message.content = content
    message_repo.save(user_message)
    if payload.attachments is not None:
        ChatExecutionService.validate_attachment_context_inputs(payload.attachments)
        user_message.attachments = message_service.replace_uploaded_items(
            message_id=user_message.id,
            uploads=payload.attachments,
            user_id=current_user.id,
        )

    assistant_message.content = ""
    assistant_message.status = "streaming"
    message_repo.save(assistant_message)

    context = await ChatExecutionService(db=db, current_user=current_user).prepare_existing_turn_execution(
        ExistingTurnExecutionInput(
            conversation=conversation,
            history_rows=history_rows[:-1],
            user_message=user_message,
            assistant_message=assistant_message,
            model_name=payload.model_name,
            system_prompt=payload.system_prompt,
            thinking_enabled=payload.thinking_enabled,
            thinking_budget=payload.thinking_budget,
            web_search_enabled=payload.web_search_enabled,
        )
    )
    return _build_streaming_response(context, provider_service, event_stream=True)
