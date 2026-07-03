import json
import re
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import quote
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.config import settings
from app.models.user import User
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.message_repo import MessageRepository
from app.repositories.project_repo import ProjectRepository
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


def _build_export_payload(
    conversation: Any,
    messages: list[Any],
    *,
    include_attachments: bool = True,
    include_context: bool = False,
) -> dict[str, Any]:
    return {
        "conversation": {
            "id": conversation.id,
            "title": conversation.title,
            "model_name": conversation.model_name,
            "system_prompt": conversation.system_prompt,
            "context_summary": conversation.context_summary if include_context else None,
            "context_summary_boundary_message_id": (
                conversation.context_summary_boundary_message_id if include_context else None
            ),
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
                        "storage_path": attachment.storage_path,
                        "created_at": _serialize_datetime(attachment.created_at),
                    }
                    for attachment in message.attachments
                ] if include_attachments else [],
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
    if conversation.get("context_summary"):
        lines.extend(["", "## Context Summary", "", conversation["context_summary"]])

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


def _parse_requested_message_ids(message_ids: str | None) -> set[str] | None:
    if not message_ids:
        return None
    requested = {item.strip() for item in message_ids.split(",") if item.strip()}
    if not requested:
        return None
    if len(requested) > 200:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="最多只能导出 200 条指定消息")
    invalid_ids = [item for item in requested if len(item) > 64]
    if invalid_ids:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="message_ids 包含非法消息 ID")
    return requested


def _filter_messages(messages: list[Any], message_ids: str | None) -> list[Any]:
    requested = _parse_requested_message_ids(message_ids)
    if not requested:
        return messages
    return [message for message in messages if message.id in requested]


def _zip_attachment_name(file_name: str, used_names: set[str]) -> str:
    safe_name = Path(file_name).name.strip() or "attachment"
    candidate = safe_name
    suffix = 2
    while candidate in used_names:
        path = Path(safe_name)
        stem = path.stem or "attachment"
        extension = path.suffix
        candidate = f"{stem}_{suffix}{extension}"
        suffix += 1
    used_names.add(candidate)
    return f"attachments/{candidate}"


def _build_zip_export(
    payload: dict[str, Any],
    markdown: str,
    export_format: str,
    *,
    include_attachment_files: bool = True,
) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, mode="w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("conversation.md", markdown)
        archive.writestr(
            "conversation.json",
            json.dumps(payload, ensure_ascii=False, indent=2),
        )
        if not include_attachment_files:
            return buffer.getvalue()

        used_attachment_names: set[str] = set()
        for message in payload["messages"]:
            for attachment in message.get("attachments") or []:
                storage_path = attachment.get("storage_path")
                if not storage_path:
                    continue
                path = Path(storage_path)
                try:
                    resolved = path.resolve()
                    upload_root = Path(settings.upload_dir).resolve()
                    if upload_root not in resolved.parents and resolved != upload_root:
                        continue
                    if not resolved.exists() or not resolved.is_file():
                        continue
                    archive.write(resolved, _zip_attachment_name(attachment.get("file_name") or "", used_attachment_names))
                except Exception:
                    continue
    return buffer.getvalue()


@router.get("", response_model=list[ConversationListItem])
def list_conversations(
    limit: int | None = Query(default=None, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ConversationListItem]:
    service = ConversationService(ConversationRepository(db), ProjectRepository(db))
    return service.list_conversations(current_user.id, limit=limit, offset=offset)


@router.post("", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
def create_conversation(
    payload: ConversationCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ConversationResponse:
    service = ConversationService(ConversationRepository(db), ProjectRepository(db))
    conversation = service.create_conversation(payload, current_user.id)
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return conversation


@router.get("/{conversation_id}", response_model=ConversationResponse)
def get_conversation(
    conversation_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ConversationResponse:
    service = ConversationService(ConversationRepository(db), ProjectRepository(db))
    conversation = service.get_conversation(conversation_id, current_user.id)
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return conversation


@router.get("/{conversation_id}/export")
def export_conversation(
    conversation_id: str,
    export_format: str = Query(default="markdown", alias="format", pattern="^(markdown|json)$"),
    message_ids: str | None = Query(default=None, max_length=8000),
    include_attachments: bool = Query(default=True),
    include_attachment_files: bool = Query(default=True),
    include_context: bool = Query(default=False),
    as_zip: bool = Query(default=False),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Response:
    conversation_repo = ConversationRepository(db)
    # 导出接口也必须先做会话归属校验；后面的 message 查询只按 conversation_id，不再带 user_id。
    conversation = conversation_repo.get_by_user(conversation_id, current_user.id)
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    messages = _filter_messages(MessageRepository(db).list_by_conversation(conversation_id), message_ids)
    payload = _build_export_payload(
        conversation,
        messages,
        include_attachments=include_attachments,
        include_context=include_context,
    )
    filename = _safe_filename(conversation.title)
    markdown = _build_markdown_export(payload)

    if as_zip:
        content = _build_zip_export(
            payload,
            markdown,
            export_format,
            include_attachment_files=include_attachment_files,
        )
        return Response(
            content=content,
            media_type="application/zip",
            headers={"content-disposition": _content_disposition(filename, "zip")},
        )

    if export_format == "json":
        # Internal filesystem paths are only used for optional zip packaging.
        for message in payload["messages"]:
            for attachment in message.get("attachments") or []:
                attachment.pop("storage_path", None)
        content = json.dumps(payload, ensure_ascii=False, indent=2)
        return Response(
            content=content,
            media_type="application/json; charset=utf-8",
            headers={"content-disposition": _content_disposition(filename, "json")},
        )

    return Response(
        content=markdown,
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
    # update 的业务权限在 ConversationService 内通过 get_by_user 收口。
    service = ConversationService(ConversationRepository(db), ProjectRepository(db))
    conversation = service.update_conversation(conversation_id, payload, current_user.id)
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation or project not found")
    return conversation


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation(
    conversation_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    # 删除会话是物理删除；service 会先断开 RAG 检索日志对会话/消息的引用。
    service = ConversationService(ConversationRepository(db), ProjectRepository(db))
    deleted = service.delete_conversation(conversation_id, current_user.id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
