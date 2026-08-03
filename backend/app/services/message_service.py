from pathlib import Path
from uuid import uuid4

from app.core.config import settings
from app.models.attachment import Attachment
from app.models.message import Message
from app.repositories.attachment_repo import AttachmentRepository
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.knowledge_repo import KnowledgeRetrievalLogRepository
from app.repositories.message_repo import MessageRepository
from app.repositories.message_repo import MessageGenerationConflict
from app.schemas.message import MessageCreate, MessageResponse
from app.schemas.upload import UploadItemReference


class MessageService:
    """消息业务层。

    这个 service 默认调用者已经校验过 conversation 属于当前用户。
    它负责消息创建/删除、附件挂载、RAG 日志断链和出站序列化。
    """

    def __init__(
        self,
        repo: MessageRepository,
        attachment_repo: AttachmentRepository | None = None,
        conversation_repo: ConversationRepository | None = None,
    ):
        self.repo = repo
        self.attachment_repo = attachment_repo
        self.conversation_repo = conversation_repo

    def list_messages(self, conversation_id: str) -> list[MessageResponse]:
        # 读取列表时修复过期 streaming 状态，是为了让服务重启/断流后的 UI 能恢复一致状态。
        stale_count = self.repo.mark_stale_streaming_messages(conversation_id)
        if stale_count:
            self._commit()
        return [self._serialize_message(item) for item in self.repo.list_by_conversation(conversation_id)]

    def create_message(
        self,
        conversation_id: str,
        payload: MessageCreate,
        *,
        commit: bool = True,
    ) -> MessageResponse:
        # 公开创建入口只接受 user 消息；assistant/system 消息由 create_system_message 给内部编排链路使用。
        message = Message(
            conversation_id=conversation_id,
            role=payload.role,
            content=payload.content,
            status="done",
        )
        created = self.repo.create(message)
        self._touch_conversation(conversation_id)
        if commit:
            self._commit()
            self.repo.db.refresh(created)
        return self._serialize_message(created)

    def create_system_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        status: str = "done",
        *,
        commit: bool = True,
    ) -> Message:
        # 内部写消息入口。名字中的 system 不是指 role=system，而是“系统内部可信调用”。
        # ChatTurnBootstrapper 会用它创建 user 和 assistant 两类消息。
        message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            status=status,
        )
        created = self.repo.create(message)
        if commit:
            self._commit()
            self.repo.db.refresh(created)
        return created

    def save_message(self, message: Message, *, commit: bool = True) -> MessageResponse:
        saved = self.repo.save(message)
        self._touch_conversation(message.conversation_id)
        if commit:
            self._commit()
            self.repo.db.refresh(saved)
        return self._serialize_message(saved)

    def save_generation_result(
        self,
        *,
        message: Message,
        generation_id: str | None = None,
        content: str,
        reasoning_content: str | None,
        external_sources: str | None,
        status: str,
    ) -> bool:
        """Persist a stream result only if this request still owns the generation."""

        persisted = self.repo.save_generation_result(
            message_id=message.id,
            generation_id=generation_id or message.generation_id,
            content=content,
            reasoning_content=reasoning_content,
            external_sources=external_sources,
            status=status,
        )
        if not persisted:
            return False
        self._touch_conversation(message.conversation_id)
        self._commit()
        return True

    def mark_generation_prepare_failed(self, message: Message, *, generation_id: str | None = None) -> bool:
        """Close a prepare-time placeholder without clobbering a newer generation."""

        persisted = self.repo.mark_generation_failed(
            message_id=message.id,
            generation_id=generation_id or message.generation_id,
        )
        if not persisted:
            return False
        self._touch_conversation(message.conversation_id)
        self._commit()
        return True

    def attach_uploaded_items(
        self,
        *,
        message_id: str,
        uploads: list[UploadItemReference],
        user_id: str,
        commit: bool = True,
    ) -> list[Attachment]:
        # 上传文件必须位于当前用户目录下。storage_key 前缀校验是防止把别人的上传挂到自己的消息上。
        if not uploads:
            return []
        if not self.attachment_repo:
            raise RuntimeError("Attachment repository is required for attaching uploads")

        attachments: list[Attachment] = []
        for item in uploads:
            storage_key = item.storage_key.strip()
            if not storage_key or not storage_key.startswith(f"{user_id}/"):
                continue

            _, _, relative_name = storage_key.partition("/")
            storage_path = Path(settings.upload_dir) / user_id / relative_name
            attachments.append(
                Attachment(
                    message_id=message_id,
                    kind=item.kind,
                    file_name=item.file_name,
                    file_ext=Path(item.file_name).suffix.lstrip(".") or None,
                    mime_type=item.mime_type,
                    file_size=item.file_size,
                    storage_path=str(storage_path),
                    parsed_text=item.parsed_text,
                )
            )

        created = self.attachment_repo.create_many(attachments)
        if commit:
            self._commit()
            for attachment in created:
                self.repo.db.refresh(attachment)
        return created

    def replace_uploaded_items(
        self,
        *,
        message_id: str,
        uploads: list[UploadItemReference],
        user_id: str,
        commit: bool = True,
    ) -> list[Attachment]:
        # 编辑上一条用户消息时使用：先删除旧附件元数据，再挂载新上传项。
        # 这里不负责删除物理文件，避免误删仍被其他引用使用的上传文件。
        if not self.attachment_repo:
            raise RuntimeError("Attachment repository is required for replacing uploads")

        self.attachment_repo.delete_by_message_id(message_id)
        attachments = self.attach_uploaded_items(
            message_id=message_id,
            uploads=uploads,
            user_id=user_id,
            commit=False,
        )
        if commit:
            self._commit()
            for attachment in attachments:
                self.repo.db.refresh(attachment)
        return attachments

    def reset_assistant_for_regeneration(self, assistant_message: Message) -> None:
        conversation_id = assistant_message.conversation_id
        try:
            self.repo.lock_conversation(conversation_id)
            # Regeneration is another way to start a generation. It must use
            # the same conversation-wide active check as a new turn, otherwise
            # an already-running newer turn could coexist with this reset.
            self.repo.ensure_no_active_streaming(conversation_id)
            locked = self.repo.lock_message(assistant_message.id, conversation_id)
            if not locked:
                raise RuntimeError("assistant message not found")
            if locked.status == "streaming":
                raise MessageGenerationConflict()
            self._reset_assistant(locked)
            self.repo.save(locked)
            self._touch_conversation(conversation_id)
            self._commit()
            self.repo.db.refresh(locked)
        except Exception:
            self.repo.db.rollback()
            raise

    def edit_and_reset_for_regeneration(
        self,
        *,
        user_message: Message,
        assistant_message: Message,
        content: str,
        uploads: list[UploadItemReference] | None,
        user_id: str,
    ) -> list[Attachment] | None:
        """原子保存编辑后的用户消息、附件替换和 assistant 重置。"""

        try:
            self.repo.lock_conversation(user_message.conversation_id)
            # Editing the previous turn also starts a new generation and must
            # not race with a newer active turn in the same conversation.
            self.repo.ensure_no_active_streaming(user_message.conversation_id)
            locked_assistant = self.repo.lock_message(
                assistant_message.id,
                user_message.conversation_id,
            )
            if not locked_assistant:
                raise RuntimeError("assistant message not found")
            if locked_assistant.status == "streaming":
                raise MessageGenerationConflict()
            user_message.content = content
            self.repo.save(user_message)
            attachments: list[Attachment] | None = None
            if uploads is not None:
                attachments = self.replace_uploaded_items(
                    message_id=user_message.id,
                    uploads=uploads,
                    user_id=user_id,
                    commit=False,
                )
                user_message.attachments = attachments

            self._reset_assistant(locked_assistant)
            self.repo.save(locked_assistant)
            self._touch_conversation(user_message.conversation_id)
            self.repo.db.commit()
            self.repo.db.refresh(user_message)
            self.repo.db.refresh(locked_assistant)
            for attachment in attachments or []:
                self.repo.db.refresh(attachment)
            return attachments
        except Exception:
            self.repo.db.rollback()
            raise

    def delete_message(self, message_id: str, conversation_id: str) -> bool:
        # 删除消息前先解除 RAG 日志中的消息引用，避免来源定位指向不存在的 message_id。
        message = self.repo.get_by_id_and_conversation(message_id, conversation_id)
        if not message:
            return False

        try:
            KnowledgeRetrievalLogRepository(self.repo.db).detach_message_links(
                message_ids=[message.id],
                commit=False,
            )
            self.repo.delete(message)
            self._touch_conversation(conversation_id)
            self.repo.db.commit()
        except Exception:
            self.repo.db.rollback()
            raise
        return True

    def bulk_delete_messages(self, conversation_id: str, message_ids: list[str]) -> int:
        # 批量删除的所有副作用都必须限定在当前 conversation_id 内。
        # 不能把客户端原始 message_ids 直接交给 RAG 日志断链，否则夹带其他会话 ID 时，
        # 虽然消息不会被删，但其他会话的来源定位会被误清空。
        try:
            messages = self.repo.list_by_ids_and_conversation(conversation_id, message_ids)
            scoped_message_ids = [message.id for message in messages]
            if scoped_message_ids:
                KnowledgeRetrievalLogRepository(self.repo.db).detach_message_links(
                    message_ids=scoped_message_ids,
                    commit=False,
                )
            deleted_count = self.repo.bulk_delete(conversation_id, scoped_message_ids)
            self._touch_conversation(conversation_id)
            self.repo.db.commit()
            return deleted_count
        except Exception:
            self.repo.db.rollback()
            raise

    def _touch_conversation(self, conversation_id: str) -> None:
        if self.conversation_repo:
            self.conversation_repo.touch(conversation_id)

    def _commit(self) -> None:
        try:
            self.repo.db.commit()
        except Exception:
            self.repo.db.rollback()
            raise

    @staticmethod
    def _reset_assistant(message: Message) -> None:
        message.generation_id = str(uuid4())
        message.content = ""
        message.reasoning_content = None
        message.external_sources = None
        message.status = "streaming"

    def _serialize_message(self, message: Message) -> MessageResponse:
        # 不直接使用 model_validate 的原因是附件需要转换成前端可再次引用的 UploadItemReference。
        return MessageResponse(
            id=message.id,
            conversation_id=message.conversation_id,
            role=message.role,
            content=message.content,
            reasoning_content=message.reasoning_content,
            external_sources=message.external_sources,
            status=message.status,
            created_at=message.created_at,
            updated_at=message.updated_at,
            attachments=[
                UploadItemReference(
                    id=attachment.id,
                    file_name=attachment.file_name,
                    mime_type=attachment.mime_type,
                    file_size=attachment.file_size,
                    kind=attachment.kind,
                    storage_key=self._build_storage_key(attachment.storage_path),
                    parsed_text=attachment.parsed_text,
                )
                for attachment in message.attachments
            ],
        )

    @staticmethod
    def _build_storage_key(storage_path: str) -> str:
        # storage_key 是相对于 upload_dir 的安全路径；解析失败时返回空字符串，避免泄露任意本地路径。
        try:
            relative_path = Path(storage_path).resolve().relative_to(Path(settings.upload_dir).resolve())
        except Exception:
            return ""

        return relative_path.as_posix()
