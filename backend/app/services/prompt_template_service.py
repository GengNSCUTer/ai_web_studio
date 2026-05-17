from app.models.prompt_template import PromptTemplate
from app.repositories.prompt_template_repo import PromptTemplateRepository
from app.schemas.prompt_template import (
    PromptTemplateCreate,
    PromptTemplateResponse,
    PromptTemplateUpdate,
)


class PromptTemplateService:
    def __init__(self, repo: PromptTemplateRepository):
        self.repo = repo

    @staticmethod
    def _normalize_optional_text(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    def list_templates(self, user_id: str) -> list[PromptTemplateResponse]:
        return [PromptTemplateResponse.model_validate(item) for item in self.repo.list_by_user(user_id)]

    def create_template(self, user_id: str, payload: PromptTemplateCreate) -> PromptTemplateResponse:
        template = PromptTemplate(
            user_id=user_id,
            project_id=payload.project_id,
            name=payload.name.strip(),
            description=self._normalize_optional_text(payload.description),
            content=payload.content.strip(),
            default_model=self._normalize_optional_text(payload.default_model),
            is_default=payload.is_default,
        )
        if template.is_default:
            self.repo.clear_default(user_id)
        saved = self.repo.save(template)
        return PromptTemplateResponse.model_validate(saved)

    def update_template(
        self,
        template_id: str,
        user_id: str,
        payload: PromptTemplateUpdate,
    ) -> PromptTemplateResponse | None:
        template = self.repo.get_by_user(template_id, user_id)
        if not template:
            return None

        data = payload.model_dump(exclude_unset=True)
        if "name" in data and data["name"] is not None:
            template.name = data["name"].strip()
        if "project_id" in data:
            template.project_id = data["project_id"]
        if "description" in data:
            template.description = self._normalize_optional_text(data["description"])
        if "content" in data and data["content"] is not None:
            template.content = data["content"].strip()
        if "default_model" in data:
            template.default_model = self._normalize_optional_text(data["default_model"])
        if "is_default" in data and data["is_default"] is not None:
            template.is_default = data["is_default"]

        if template.is_default:
            self.repo.clear_default(user_id, exclude_template_id=template.id)

        saved = self.repo.save(template)
        return PromptTemplateResponse.model_validate(saved)

    def delete_template(self, template_id: str, user_id: str) -> bool:
        template = self.repo.get_by_user(template_id, user_id)
        if not template:
            return False
        self.repo.delete(template)
        return True
