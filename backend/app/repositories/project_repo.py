from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.conversation import Conversation
from app.models.prompt_template import PromptTemplate
from app.models.project import Project


class ProjectRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_by_user(self, user_id: str) -> list[Project]:
        stmt = (
            select(Project)
            .where(Project.user_id == user_id)
            .order_by(Project.updated_at.desc(), Project.created_at.desc())
        )
        return list(self.db.scalars(stmt).all())

    def get_by_user(self, project_id: str, user_id: str) -> Project | None:
        stmt = select(Project).where(Project.id == project_id, Project.user_id == user_id).limit(1)
        return self.db.scalars(stmt).first()

    def save(self, project: Project) -> Project:
        self.db.add(project)
        self.db.commit()
        self.db.refresh(project)
        return project

    def delete(self, project: Project) -> None:
        self.db.execute(
            update(Conversation)
            .where(Conversation.project_id == project.id)
            .values(project_id=None)
        )
        self.db.execute(
            update(PromptTemplate)
            .where(PromptTemplate.project_id == project.id)
            .values(project_id=None)
        )
        self.db.delete(project)
        self.db.commit()
