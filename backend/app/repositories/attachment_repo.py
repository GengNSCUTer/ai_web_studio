from sqlalchemy.orm import Session
from sqlalchemy import select

from app.models.attachment import Attachment


class AttachmentRepository:
    def __init__(self, db: Session):
        self.db = db

    def create_many(self, attachments: list[Attachment]) -> list[Attachment]:
        if not attachments:
            return []

        self.db.add_all(attachments)
        self.db.commit()

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
        self.db.commit()
        for attachment in attachments:
            self.db.refresh(attachment)
        return attachments
