from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.project_file import ProjectFile


class ProjectFileRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_by_project(self, project_id: str, user_id: str) -> list[ProjectFile]:
        stmt = (
            select(ProjectFile)
            .where(ProjectFile.project_id == project_id, ProjectFile.user_id == user_id)
            .order_by(ProjectFile.created_at.desc())
        )
        return list(self.db.scalars(stmt).all())

    def get_by_user(self, file_id: str, user_id: str) -> ProjectFile | None:
        stmt = select(ProjectFile).where(ProjectFile.id == file_id, ProjectFile.user_id == user_id).limit(1)
        return self.db.scalars(stmt).first()

    def save(self, project_file: ProjectFile) -> ProjectFile:
        self.db.add(project_file)
        self.db.commit()
        self.db.refresh(project_file)
        return project_file

    def delete(self, project_file: ProjectFile) -> None:
        self.db.delete(project_file)
        self.db.commit()

    def delete_by_project(self, project_id: str) -> int:
        result = self.db.execute(delete(ProjectFile).where(ProjectFile.project_id == project_id))
        self.db.commit()
        return int(result.rowcount or 0)

    def total_size_by_project(self, project_id: str, user_id: str) -> int:
        stmt = select(func.coalesce(func.sum(ProjectFile.file_size), 0)).where(
            ProjectFile.project_id == project_id,
            ProjectFile.user_id == user_id,
        )
        return int(self.db.scalar(stmt) or 0)
