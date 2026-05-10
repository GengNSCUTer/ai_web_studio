from pathlib import Path

from app.core.config import settings
from app.models.attachment import Attachment
from app.models.message import Message
from app.repositories.attachment_repo import AttachmentRepository
from app.repositories.message_repo import MessageRepository
from app.schemas.message import MessageCreate, MessageResponse
from app.schemas.upload import UploadItemReference


class MessageService:
    def __init__(self, repo: MessageRepository, attachment_repo: AttachmentRepository | None = None):
        self.repo = repo
        self.attachment_repo = attachment_repo

    def list_messages(self, conversation_id: str) -> list[MessageResponse]:
        self.repo.mark_stale_streaming_messages(conversation_id)
        return [self._serialize_message(item) for item in self.repo.list_by_conversation(conversation_id)]

    def create_message(self, conversation_id: str, payload: MessageCreate) -> MessageResponse:
        message = Message(
            conversation_id=conversation_id,
            role=payload.role,
            content=payload.content,
            status="done",
        )
        created = self.repo.create(message)
        return self._serialize_message(created)

    def create_system_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        status: str = "done",
    ) -> Message:
        message = Message(
            conversation_id=conversation_id,
            role=role,
            content=content,
            status=status,
        )
        return self.repo.create(message)

    def save_message(self, message: Message) -> MessageResponse:
        saved = self.repo.save(message)
        return self._serialize_message(saved)

    def attach_uploaded_items(
        self,
        *,
        message_id: str,
        uploads: list[UploadItemReference],
        user_id: str,
    ) -> list[Attachment]:
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

        return self.attachment_repo.create_many(attachments)

    def delete_message(self, message_id: str, conversation_id: str) -> bool:
        message = self.repo.get_by_id_and_conversation(message_id, conversation_id)
        if not message:
            return False

        self.repo.delete(message)
        return True

    def bulk_delete_messages(self, conversation_id: str, message_ids: list[str]) -> int:
        return self.repo.bulk_delete(conversation_id, message_ids)

    def _serialize_message(self, message: Message) -> MessageResponse:
        return MessageResponse(
            id=message.id,
            conversation_id=message.conversation_id,
            role=message.role,
            content=message.content,
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
        try:
            relative_path = Path(storage_path).resolve().relative_to(Path(settings.upload_dir).resolve())
        except Exception:
            return ""

        return relative_path.as_posix()
