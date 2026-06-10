from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.knowledge import KnowledgeBase, KnowledgeChunk, KnowledgeDocument, KnowledgeJob


class KnowledgeBaseRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_by_user(self, user_id: str) -> list[KnowledgeBase]:
        stmt = (
            select(KnowledgeBase)
            .where(KnowledgeBase.user_id == user_id)
            .order_by(KnowledgeBase.updated_at.desc(), KnowledgeBase.created_at.desc())
        )
        return list(self.db.scalars(stmt).all())

    def get_by_user(self, knowledge_base_id: str, user_id: str) -> KnowledgeBase | None:
        stmt = (
            select(KnowledgeBase)
            .where(KnowledgeBase.id == knowledge_base_id, KnowledgeBase.user_id == user_id)
            .limit(1)
        )
        return self.db.scalars(stmt).first()

    def save(self, knowledge_base: KnowledgeBase) -> KnowledgeBase:
        self.db.add(knowledge_base)
        self.db.commit()
        self.db.refresh(knowledge_base)
        return knowledge_base

    def delete(self, knowledge_base: KnowledgeBase) -> None:
        self.db.delete(knowledge_base)
        self.db.commit()

    def document_count(self, knowledge_base_id: str, user_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(KnowledgeDocument)
            .where(
                KnowledgeDocument.knowledge_base_id == knowledge_base_id,
                KnowledgeDocument.user_id == user_id,
            )
        )
        return int(self.db.scalar(stmt) or 0)


class KnowledgeDocumentRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_by_knowledge_base(self, knowledge_base_id: str, user_id: str) -> list[KnowledgeDocument]:
        stmt = (
            select(KnowledgeDocument)
            .where(
                KnowledgeDocument.knowledge_base_id == knowledge_base_id,
                KnowledgeDocument.user_id == user_id,
            )
            .order_by(KnowledgeDocument.created_at.desc())
        )
        return list(self.db.scalars(stmt).all())

    def get_by_user(self, document_id: str, user_id: str) -> KnowledgeDocument | None:
        stmt = (
            select(KnowledgeDocument)
            .where(KnowledgeDocument.id == document_id, KnowledgeDocument.user_id == user_id)
            .limit(1)
        )
        return self.db.scalars(stmt).first()

    def save(self, document: KnowledgeDocument) -> KnowledgeDocument:
        self.db.add(document)
        self.db.commit()
        self.db.refresh(document)
        return document

    def delete(self, document: KnowledgeDocument) -> None:
        self.db.delete(document)
        self.db.commit()


class KnowledgeChunkRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_by_document(self, document_id: str, user_id: str) -> list[KnowledgeChunk]:
        stmt = (
            select(KnowledgeChunk)
            .where(KnowledgeChunk.document_id == document_id, KnowledgeChunk.user_id == user_id)
            .order_by(KnowledgeChunk.chunk_index.asc())
        )
        return list(self.db.scalars(stmt).all())

    def list_by_knowledge_base(self, knowledge_base_id: str, user_id: str) -> list[KnowledgeChunk]:
        stmt = (
            select(KnowledgeChunk)
            .where(KnowledgeChunk.knowledge_base_id == knowledge_base_id, KnowledgeChunk.user_id == user_id)
            .order_by(KnowledgeChunk.vector_id.asc())
        )
        return list(self.db.scalars(stmt).all())

    def count_by_knowledge_base(self, knowledge_base_id: str, user_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(KnowledgeChunk.knowledge_base_id == knowledge_base_id, KnowledgeChunk.user_id == user_id)
        )
        return int(self.db.scalar(stmt) or 0)

    def max_vector_id(self, knowledge_base_id: str, user_id: str) -> int:
        stmt = (
            select(func.max(KnowledgeChunk.vector_id))
            .where(KnowledgeChunk.knowledge_base_id == knowledge_base_id, KnowledgeChunk.user_id == user_id)
        )
        return int(self.db.scalar(stmt) or 0)

    def replace_document_chunks(self, document_id: str, user_id: str, chunks: list[KnowledgeChunk]) -> list[KnowledgeChunk]:
        stmt = select(KnowledgeChunk).where(KnowledgeChunk.document_id == document_id, KnowledgeChunk.user_id == user_id)
        existing = list(self.db.scalars(stmt).all())
        for item in existing:
            self.db.delete(item)
        for chunk in chunks:
            self.db.add(chunk)
        self.db.commit()
        for chunk in chunks:
            self.db.refresh(chunk)
        return chunks


class KnowledgeJobRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_by_knowledge_base(self, knowledge_base_id: str, user_id: str) -> list[KnowledgeJob]:
        stmt = (
            select(KnowledgeJob)
            .where(KnowledgeJob.knowledge_base_id == knowledge_base_id, KnowledgeJob.user_id == user_id)
            .order_by(KnowledgeJob.created_at.desc())
        )
        return list(self.db.scalars(stmt).all())

    def latest_by_document_type(
        self,
        *,
        document_id: str,
        user_id: str,
        job_type: str,
    ) -> KnowledgeJob | None:
        stmt = (
            select(KnowledgeJob)
            .where(
                KnowledgeJob.document_id == document_id,
                KnowledgeJob.user_id == user_id,
                KnowledgeJob.job_type == job_type,
            )
            .order_by(KnowledgeJob.created_at.desc())
            .limit(1)
        )
        return self.db.scalars(stmt).first()

    def save(self, job: KnowledgeJob) -> KnowledgeJob:
        self.db.add(job)
        self.db.commit()
        self.db.refresh(job)
        return job
