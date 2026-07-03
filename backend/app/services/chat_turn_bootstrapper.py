from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, status

from app.repositories.conversation_repo import ConversationRepository
from app.repositories.message_repo import MessageRepository
from app.repositories.project_repo import ProjectRepository
from app.schemas.conversation import ConversationCreate
from app.schemas.message import ChatStreamRequest
from app.services.conversation_service import ConversationService
from app.services.message_service import MessageService


@dataclass
class NewTurnBootstrapResult:
    """新一轮聊天落库后的最小结果。

    history_rows 是创建 assistant 占位消息之前的历史，后续 prompt 组装用它代表“本轮用户问题之前的上下文”。
    """

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
    """负责一轮聊天开始前的数据库准备。

    第一性原则：模型调用之前，必须先确定这轮话属于哪个 conversation，
    并且把 user message 和 assistant streaming 占位消息写入数据库。
    这样前端刷新/切会话/断流后仍能看到这轮对话的状态。
    """

    def __init__(
        self,
        *,
        conversation_repo: ConversationRepository,
        project_repo: ProjectRepository | None = None,
        message_repo: MessageRepository,
        message_service: MessageService,
        user_id: str,
    ) -> None:
        self.conversation_repo = conversation_repo
        self.project_repo = project_repo
        self.message_repo = message_repo
        self.message_service = message_service
        self.user_id = user_id

    @staticmethod
    def validate_attachment_context_inputs(attachments: list[object]) -> None:
        # 这里校验的是“附件能否进入上下文”，不是“附件能否上传”。
        # 图片可以作为多模态输入，文档必须已经解析出 parsed_text 才能参与文本上下文。
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
        # 如果传了 conversation_id，本轮必须落在当前用户自己的会话里。
        # 如果没传，则按当前输入创建新会话，并使用用户默认模型/系统提示兜底。
        conversation = None
        if payload.conversation_id:
            conversation = self.conversation_repo.get_by_user(payload.conversation_id, self.user_id)
            if not conversation:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

        if not conversation:
            conversation_response = ConversationService(self.conversation_repo, self.project_repo).create_conversation(
                ConversationCreate(
                    title=derive_title(payload.title, payload.content),
                    model_name=clean_optional_str(payload.model_name)
                    or clean_optional_str(getattr(default_settings, "default_model", None)),
                    system_prompt=clean_optional_str(payload.system_prompt)
                    or clean_optional_str(getattr(default_settings, "system_prompt", None)),
                ),
                self.user_id,
            )
            if not conversation_response:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
            conversation = self.conversation_repo.get_by_user(conversation_response.id, self.user_id)
            if not conversation:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Conversation bootstrap failed",
                )

        if conversation.title == "New Chat" and payload.content.strip():
            # 第一次有效提问时用问题前缀生成标题，避免大量会话都叫 New Chat。
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

        # 注意这里的 history_rows 包含刚创建的 user_message，但不包含下面即将创建的 assistant 占位消息。
        # 这保证 prompt 组装时当前用户问题会进入历史，而空 assistant 占位不会被发给模型。
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
        # 单轮请求可以临时覆盖会话 system_prompt/model_name。
        # 当前实现会持久化覆盖结果，因此它不是“只对本次请求生效”的临时参数。
        cleaned_system_prompt = clean_optional_str(system_prompt)
        cleaned_model_name = clean_optional_str(model_name)

        if system_prompt is not None:
            conversation.system_prompt = cleaned_system_prompt
            self.conversation_repo.save(conversation)

        if cleaned_model_name is not None and cleaned_model_name != conversation.model_name:
            conversation.model_name = cleaned_model_name
            self.conversation_repo.save(conversation)
