from sqlalchemy.orm import Session
from sqlalchemy import delete, select

from app.models.attachment import Attachment


class AttachmentRepository:
    def __init__(self, db: Session):
        self.db = db

    def create_many(self, attachments: list[Attachment]) -> list[Attachment]:
        if not attachments:
            return []

        self.db.add_all(attachments)
        self.db.flush()

        for attachment in attachments:
            self.db.refresh(attachment)
        return attachments

    def list_by_storage_paths(self, storage_paths: list[str]) -> list[Attachment]:
        if not storage_paths:
            return []

        stmt = select(Attachment).where(Attachment.storage_path.in_(storage_paths))
        return list(self.db.scalars(stmt).all())

    def save_many(self, attachments: list[Attachment]) -> list[Attachment]:
        if not attachments:
            return []

        self.db.add_all(attachments)
        self.db.flush()
        for attachment in attachments:
            self.db.refresh(attachment)
        return attachments

    def delete_by_message_id(self, message_id: str) -> int:
        stmt = delete(Attachment).where(Attachment.message_id == message_id)
        result = self.db.execute(stmt)
        self.db.flush()
        return int(result.rowcount or 0)
