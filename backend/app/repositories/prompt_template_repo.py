from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.prompt_template import PromptTemplate


class PromptTemplateRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_by_user(self, user_id: str) -> list[PromptTemplate]:
        stmt = (
            select(PromptTemplate)
            .where(PromptTemplate.user_id == user_id)
            .order_by(
                PromptTemplate.is_default.desc(),
                PromptTemplate.updated_at.desc(),
                PromptTemplate.created_at.desc(),
            )
        )
        return list(self.db.scalars(stmt).all())

    def get_by_user(self, template_id: str, user_id: str) -> PromptTemplate | None:
        stmt = (
            select(PromptTemplate)
            .where(PromptTemplate.id == template_id, PromptTemplate.user_id == user_id)
            .limit(1)
        )
        return self.db.scalars(stmt).first()

    def save(self, template: PromptTemplate) -> PromptTemplate:
        self.db.add(template)
        self.db.commit()
        self.db.refresh(template)
        return template

    def clear_default(self, user_id: str, exclude_template_id: str | None = None) -> None:
        templates = self.list_by_user(user_id)
        changed = False
        for template in templates:
            if exclude_template_id and template.id == exclude_template_id:
                continue
            if template.is_default:
                template.is_default = False
                self.db.add(template)
                changed = True
        if changed:
            self.db.commit()

    def delete(self, template: PromptTemplate) -> None:
        self.db.delete(template)
        self.db.commit()
