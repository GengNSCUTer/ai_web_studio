from datetime import datetime, timezone

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.user_memory import MemoryExtractionJob


class MemoryExtractionJobRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_idempotency_key(self, key: str) -> MemoryExtractionJob | None:
        return self.db.scalars(
            select(MemoryExtractionJob).where(MemoryExtractionJob.idempotency_key == key).limit(1)
        ).first()

    def get_by_user(self, job_id: str, user_id: str) -> MemoryExtractionJob | None:
        return self.db.scalars(
            select(MemoryExtractionJob)
            .where(MemoryExtractionJob.id == job_id, MemoryExtractionJob.user_id == user_id)
            .limit(1)
        ).first()

    def list_by_user(self, user_id: str, limit: int = 30) -> list[MemoryExtractionJob]:
        return list(
            self.db.scalars(
                select(MemoryExtractionJob)
                .where(MemoryExtractionJob.user_id == user_id)
                .order_by(MemoryExtractionJob.created_at.desc())
                .limit(limit)
            ).all()
        )

    def claim_next(self, owner: str, *, lease_seconds: int = 90) -> MemoryExtractionJob | None:
        now = datetime.now(timezone.utc)
        statement = (
            select(MemoryExtractionJob)
            .where(
                or_(
                    MemoryExtractionJob.status == "pending",
                    (MemoryExtractionJob.status == "running")
                    & (MemoryExtractionJob.lease_expires_at < now),
                ),
                or_(MemoryExtractionJob.available_at.is_(None), MemoryExtractionJob.available_at <= now),
            )
            .order_by(MemoryExtractionJob.created_at.asc())
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        return self.db.scalars(statement).first()
