from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.repositories.prompt_template_repo import PromptTemplateRepository
from app.schemas.prompt_template import (
    PromptTemplateCreate,
    PromptTemplateResponse,
    PromptTemplateUpdate,
)
from app.services.prompt_template_service import PromptTemplateService

router = APIRouter(prefix="/prompt-templates", tags=["prompt-templates"])


@router.get("", response_model=list[PromptTemplateResponse])
def list_prompt_templates(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[PromptTemplateResponse]:
    service = PromptTemplateService(PromptTemplateRepository(db))
    return service.list_templates(current_user.id)


@router.post("", response_model=PromptTemplateResponse, status_code=status.HTTP_201_CREATED)
def create_prompt_template(
    payload: PromptTemplateCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PromptTemplateResponse:
    service = PromptTemplateService(PromptTemplateRepository(db))
    return service.create_template(current_user.id, payload)


@router.patch("/{template_id}", response_model=PromptTemplateResponse)
def update_prompt_template(
    template_id: str,
    payload: PromptTemplateUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> PromptTemplateResponse:
    service = PromptTemplateService(PromptTemplateRepository(db))
    template = service.update_template(template_id, current_user.id, payload)
    if not template:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prompt template not found")
    return template


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_prompt_template(
    template_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    service = PromptTemplateService(PromptTemplateRepository(db))
    deleted = service.delete_template(template_id, current_user.id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prompt template not found")
