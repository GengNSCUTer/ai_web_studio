import json
import re
from datetime import datetime
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.message_repo import MessageRepository
from app.schemas.conversation import (
    ConversationCreate,
    ConversationListItem,
    ConversationResponse,
    ConversationUpdate,
)
from app.services.conversation_service import ConversationService

router = APIRouter(prefix="/conversations", tags=["conversations"])


def _serialize_datetime(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _safe_filename(value: str) -> str:
    normalized = re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", value.strip(), flags=re.UNICODE)
    return (normalized.strip("._") or "conversation")[:80]


def _content_disposition(filename: str, extension: str) -> str:
    utf8_filename = f"{filename}.{extension}"
    encoded_filename = quote(utf8_filename)
    return f"attachment; filename=\"conversation.{extension}\"; filename*=UTF-8''{encoded_filename}"


def _build_export_payload(conversation: Any, messages: list[Any]) -> dict[str, Any]:
    return {
        "conversation": {
            "id": conversation.id,
            "title": conversation.title,
            "model_name": conversation.model_name,
            "system_prompt": conversation.system_prompt,
            "created_at": _serialize_datetime(conversation.created_at),
            "updated_at": _serialize_datetime(conversation.updated_at),
        },
        "messages": [
            {
                "id": message.id,
                "role": message.role,
                "content": message.content,
                "status": message.status,
                "created_at": _serialize_datetime(message.created_at),
                "updated_at": _serialize_datetime(message.updated_at),
                "attachments": [
                    {
                        "id": attachment.id,
                        "kind": attachment.kind,
                        "file_name": attachment.file_name,
                        "mime_type": attachment.mime_type,
                        "file_size": attachment.file_size,
                        "created_at": _serialize_datetime(attachment.created_at),
                    }
                    for attachment in message.attachments
                ],
            }
            for message in messages
        ],
    }


def _build_markdown_export(payload: dict[str, Any]) -> str:
    conversation = payload["conversation"]
    lines = [
        f"# {conversation['title']}",
        "",
        f"- 会话 ID：`{conversation['id']}`",
        f"- 模型：`{conversation['model_name']}`",
        f"- 创建时间：{conversation['created_at'] or '--'}",
        f"- 更新时间：{conversation['updated_at'] or '--'}",
    ]
    if conversation.get("system_prompt"):
        lines.extend(["", "## System Prompt", "", conversation["system_prompt"]])

    lines.append("")
    lines.append("## Messages")
    for message in payload["messages"]:
        role = message["role"].upper()
        lines.extend(
            [
                "",
                f"### {role} · {message['created_at'] or '--'}",
                "",
                message["content"] or "",
            ]
        )
        attachments = message.get("attachments") or []
        if attachments:
            lines.extend(["", "附件："])
            for attachment in attachments:
                size = attachment["file_size"] if attachment["file_size"] is not None else "--"
                lines.append(
                    f"- `{attachment['file_name']}` · {attachment['kind']} · {attachment['mime_type'] or '--'} · {size} bytes"
                )

    lines.append("")
    return "\n".join(lines)


@router.get("", response_model=list[ConversationListItem])
def list_conversations(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ConversationListItem]:
    service = ConversationService(ConversationRepository(db))
    return service.list_conversations(current_user.id)


@router.post("", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
def create_conversation(
    payload: ConversationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ConversationResponse:
    service = ConversationService(ConversationRepository(db))
    return service.create_conversation(payload, current_user.id)


@router.get("/{conversation_id}", response_model=ConversationResponse)
def get_conversation(
    conversation_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ConversationResponse:
    service = ConversationService(ConversationRepository(db))
    conversation = service.get_conversation(conversation_id, current_user.id)
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return conversation


@router.get("/{conversation_id}/export")
def export_conversation(
    conversation_id: str,
    export_format: str = Query(default="markdown", alias="format", pattern="^(markdown|json)$"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    conversation_repo = ConversationRepository(db)
    conversation = conversation_repo.get_by_user(conversation_id, current_user.id)
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    messages = MessageRepository(db).list_by_conversation(conversation_id)
    payload = _build_export_payload(conversation, messages)
    filename = _safe_filename(conversation.title)

    if export_format == "json":
        content = json.dumps(payload, ensure_ascii=False, indent=2)
        return Response(
            content=content,
            media_type="application/json; charset=utf-8",
            headers={"content-disposition": _content_disposition(filename, "json")},
        )

    content = _build_markdown_export(payload)
    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers={"content-disposition": _content_disposition(filename, "md")},
    )


@router.patch("/{conversation_id}", response_model=ConversationResponse)
def update_conversation(
    conversation_id: str,
    payload: ConversationUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ConversationResponse:
    service = ConversationService(ConversationRepository(db))
    conversation = service.update_conversation(conversation_id, payload, current_user.id)
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return conversation


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conversation_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    service = ConversationService(ConversationRepository(db))
    deleted = service.delete_conversation(conversation_id, current_user.id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
