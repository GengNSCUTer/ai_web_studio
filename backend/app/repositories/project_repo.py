from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.conversation import Conversation
from app.models.message import Message
from app.models.project_file import ProjectFile
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

    def stats(self, project_id: str, user_id: str) -> dict[str, int]:
        conversation_count = int(
            self.db.scalar(
                select(func.count())
                .select_from(Conversation)
                .where(Conversation.project_id == project_id, Conversation.user_id == user_id)
            )
            or 0
        )
        message_count = int(
            self.db.scalar(
                select(func.count())
                .select_from(Message)
                .join(Conversation, Message.conversation_id == Conversation.id)
                .where(Conversation.project_id == project_id, Conversation.user_id == user_id)
            )
            or 0
        )
        file_count = int(
            self.db.scalar(
                select(func.count())
                .select_from(ProjectFile)
                .where(ProjectFile.project_id == project_id, ProjectFile.user_id == user_id)
            )
            or 0
        )
        prompt_template_count = int(
            self.db.scalar(
                select(func.count())
                .select_from(PromptTemplate)
                .where(PromptTemplate.project_id == project_id, PromptTemplate.user_id == user_id)
            )
            or 0
        )
        total_file_size = int(
            self.db.scalar(
                select(func.coalesce(func.sum(ProjectFile.file_size), 0)).where(
                    ProjectFile.project_id == project_id,
                    ProjectFile.user_id == user_id,
                )
            )
            or 0
        )
        return {
            "conversation_count": conversation_count,
            "message_count": message_count,
            "file_count": file_count,
            "prompt_template_count": prompt_template_count,
            "total_file_size": total_file_size,
        }
