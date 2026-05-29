from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, status

from app.repositories.conversation_repo import ConversationRepository
from app.repositories.message_repo import MessageRepository
from app.schemas.conversation import ConversationCreate
from app.schemas.message import ChatStreamRequest
from app.services.conversation_service import ConversationService
from app.services.message_service import MessageService


@dataclass
class NewTurnBootstrapResult:
    conversation: object
    user_message: object
    assistant_message: object
    history_rows: list[object]


def clean_optional_str(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def derive_title(title: str | None, content: str) -> str:
    if title:
        return title
    normalized = " ".join(content.strip().split())
    return (normalized[:30] or "New Chat").strip()


def is_supported_text_file(attachment: object) -> bool:
    file_name = (getattr(attachment, "file_name", None) or "").strip().lower()
    ext = Path(file_name).suffix.lstrip(".")
    return ext in {"txt", "md", "markdown", "pdf", "docx"}


class ChatTurnBootstrapper:
    """Creates or prepares a chat turn before context assembly starts."""

    def __init__(
        self,
        *,
        conversation_repo: ConversationRepository,
        message_repo: MessageRepository,
        message_service: MessageService,
        user_id: str,
    ) -> None:
        self.conversation_repo = conversation_repo
        self.message_repo = message_repo
        self.message_service = message_service
        self.user_id = user_id

    @staticmethod
    def validate_attachment_context_inputs(attachments: list[object]) -> None:
        unsupported_attachments = [
            item
            for item in attachments
            if getattr(item, "kind", None) == "file" and not is_supported_text_file(item)
        ]
        if unsupported_attachments:
            unsupported_names = "、".join(
                getattr(item, "file_name", "未知文件") for item in unsupported_attachments
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"当前仅支持 txt、md、pdf 文档进入上下文，暂不支持：{unsupported_names}",
            )

        attachments_missing_text = [
            item
            for item in attachments
            if getattr(item, "kind", None) == "file" and not (getattr(item, "parsed_text", None) or "").strip()
        ]
        if attachments_missing_text:
            missing_names = "、".join(
                getattr(item, "file_name", "未知文件") for item in attachments_missing_text
            )
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"以下文档未解析到有效文本，暂时无法进入上下文：{missing_names}",
            )

    def bootstrap_new_turn(self, *, payload: ChatStreamRequest, default_settings: object) -> NewTurnBootstrapResult:
        conversation = None
        if payload.conversation_id:
            conversation = self.conversation_repo.get_by_user(payload.conversation_id, self.user_id)
            if not conversation:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

        if not conversation:
            conversation_response = ConversationService(self.conversation_repo).create_conversation(
                ConversationCreate(
                    title=derive_title(payload.title, payload.content),
                    model_name=clean_optional_str(payload.model_name)
                    or clean_optional_str(getattr(default_settings, "default_model", None)),
                    system_prompt=clean_optional_str(payload.system_prompt)
                    or clean_optional_str(getattr(default_settings, "system_prompt", None)),
                ),
                self.user_id,
            )
            conversation = self.conversation_repo.get_by_user(conversation_response.id, self.user_id)
            if not conversation:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Conversation bootstrap failed",
                )

        if conversation.title == "New Chat" and payload.content.strip():
            conversation.title = derive_title(payload.title, payload.content)
            self.conversation_repo.save(conversation)

        self.apply_turn_overrides(
            conversation=conversation,
            model_name=payload.model_name,
            system_prompt=payload.system_prompt,
        )
        self.validate_attachment_context_inputs(payload.attachments)

        user_message = self.message_service.create_system_message(
            conversation_id=conversation.id,
            role="user",
            content=payload.content,
            status="done",
        )
        attachments = self.message_service.attach_uploaded_items(
            message_id=user_message.id,
            uploads=payload.attachments,
            user_id=self.user_id,
        )
        if attachments:
            user_message.attachments = attachments

        history_rows = self.message_repo.list_by_conversation(conversation.id)
        assistant_message = self.message_service.create_system_message(
            conversation_id=conversation.id,
            role="assistant",
            content="",
            status="streaming",
        )
        self.conversation_repo.touch(conversation.id)

        return NewTurnBootstrapResult(
            conversation=conversation,
            user_message=user_message,
            assistant_message=assistant_message,
            history_rows=history_rows,
        )

    def apply_turn_overrides(
        self,
        *,
        conversation: object,
        model_name: str | None,
        system_prompt: str | None,
    ) -> None:
        cleaned_system_prompt = clean_optional_str(system_prompt)
        cleaned_model_name = clean_optional_str(model_name)

        if system_prompt is not None:
            conversation.system_prompt = cleaned_system_prompt
            self.conversation_repo.save(conversation)

        if cleaned_model_name is not None and cleaned_model_name != conversation.model_name:
            conversation.model_name = cleaned_model_name
            self.conversation_repo.save(conversation)
