from app.models.project import Project
from app.repositories.project_repo import ProjectRepository
from app.schemas.project import ProjectCreate, ProjectResponse, ProjectUpdate


class ProjectService:
    def __init__(self, repo: ProjectRepository):
        self.repo = repo

    @staticmethod
    def _normalize_optional_text(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    def list_projects(self, user_id: str) -> list[ProjectResponse]:
        return [ProjectResponse.model_validate(item) for item in self.repo.list_by_user(user_id)]

    def create_project(self, user_id: str, payload: ProjectCreate) -> ProjectResponse:
        project = Project(
            user_id=user_id,
            name=payload.name.strip(),
            description=self._normalize_optional_text(payload.description),
            default_model=self._normalize_optional_text(payload.default_model),
            system_prompt=self._normalize_optional_text(payload.system_prompt),
        )
        return ProjectResponse.model_validate(self.repo.save(project))

    def update_project(self, project_id: str, user_id: str, payload: ProjectUpdate) -> ProjectResponse | None:
        project = self.repo.get_by_user(project_id, user_id)
        if not project:
            return None

        data = payload.model_dump(exclude_unset=True)
        if "name" in data and data["name"] is not None:
            project.name = data["name"].strip()
        if "description" in data:
            project.description = self._normalize_optional_text(data["description"])
        if "default_model" in data:
            project.default_model = self._normalize_optional_text(data["default_model"])
        if "system_prompt" in data:
            project.system_prompt = self._normalize_optional_text(data["system_prompt"])
        return ProjectResponse.model_validate(self.repo.save(project))

    def delete_project(self, project_id: str, user_id: str) -> bool:
        project = self.repo.get_by_user(project_id, user_id)
        if not project:
            return False
        self.repo.delete(project)
        return True
