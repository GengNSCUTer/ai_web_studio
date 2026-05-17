from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.repositories.project_file_repo import ProjectFileRepository
from app.repositories.project_repo import ProjectRepository
from app.schemas.project import (
    ProjectCreate,
    ProjectFileCreate,
    ProjectFileResponse,
    ProjectResponse,
    ProjectStatsResponse,
    ProjectUpdate,
)
from app.services.project_service import ProjectFileService, ProjectService

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[ProjectResponse])
def list_projects(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ProjectResponse]:
    return ProjectService(ProjectRepository(db)).list_projects(current_user.id)


@router.post("", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(
    payload: ProjectCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ProjectResponse:
    return ProjectService(ProjectRepository(db)).create_project(current_user.id, payload)


@router.patch("/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: str,
    payload: ProjectUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ProjectResponse:
    project = ProjectService(ProjectRepository(db)).update_project(project_id, current_user.id, payload)
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


@router.get("/{project_id}/stats", response_model=ProjectStatsResponse)
def get_project_stats(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ProjectStatsResponse:
    stats = ProjectService(ProjectRepository(db)).get_stats(project_id, current_user.id)
    if not stats:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return stats


@router.get("/{project_id}/files", response_model=list[ProjectFileResponse])
def list_project_files(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[ProjectFileResponse]:
    files = ProjectFileService(ProjectFileRepository(db), ProjectRepository(db)).list_files(project_id, current_user.id)
    if files is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return files


@router.post("/{project_id}/files", response_model=ProjectFileResponse, status_code=status.HTTP_201_CREATED)
def add_project_file(
    project_id: str,
    payload: ProjectFileCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ProjectFileResponse:
    if not payload.storage_key.startswith(f"{current_user.id}/"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid storage key")
    file_item = ProjectFileService(ProjectFileRepository(db), ProjectRepository(db)).add_file(
        project_id,
        current_user.id,
        payload,
    )
    if not file_item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return file_item


@router.delete("/{project_id}/files/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project_file(
    project_id: str,
    file_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    deleted = ProjectFileService(ProjectFileRepository(db), ProjectRepository(db)).delete_file(
        project_id,
        file_id,
        current_user.id,
    )
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project file not found")


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    deleted = ProjectService(ProjectRepository(db)).delete_project(project_id, current_user.id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
