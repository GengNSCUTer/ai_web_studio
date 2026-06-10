from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.repositories.knowledge_repo import (
    KnowledgeBaseRepository,
    KnowledgeDocumentRepository,
    KnowledgeJobRepository,
)
from app.repositories.project_repo import ProjectRepository
from app.repositories.tool_config_repo import ToolConfigRepository
from app.schemas.knowledge import (
    KnowledgeBaseCreate,
    KnowledgeBaseResponse,
    KnowledgeBaseUpdate,
    KnowledgeConnectionTestResponse,
    KnowledgeCredentialResponse,
    KnowledgeCredentialUpdate,
    KnowledgeDocumentCreate,
    KnowledgeDocumentParseResponse,
    KnowledgeDocumentResponse,
    KnowledgeJobResponse,
    KnowledgeMarkdownPreviewResponse,
)
from app.services.knowledge_service import (
    KnowledgeBaseService,
    KnowledgeCredentialService,
    KnowledgeDocumentService,
    KnowledgeJobService,
)

router = APIRouter(prefix="/knowledge-bases", tags=["knowledge"])
credential_router = APIRouter(prefix="/knowledge", tags=["knowledge"])


def _base_service(db: Session) -> KnowledgeBaseService:
    return KnowledgeBaseService(KnowledgeBaseRepository(db), ProjectRepository(db))


def _document_service(db: Session) -> KnowledgeDocumentService:
    return KnowledgeDocumentService(
        KnowledgeDocumentRepository(db),
        KnowledgeBaseRepository(db),
        KnowledgeJobRepository(db),
    )


def _job_service(db: Session) -> KnowledgeJobService:
    return KnowledgeJobService(KnowledgeJobRepository(db), KnowledgeBaseRepository(db))


def _credential_service(db: Session) -> KnowledgeCredentialService:
    return KnowledgeCredentialService(ToolConfigRepository(db))


@router.get("", response_model=list[KnowledgeBaseResponse])
def list_knowledge_bases(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[KnowledgeBaseResponse]:
    return _base_service(db).list_knowledge_bases(current_user.id)


@router.post("", response_model=KnowledgeBaseResponse, status_code=status.HTTP_201_CREATED)
def create_knowledge_base(
    payload: KnowledgeBaseCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> KnowledgeBaseResponse:
    try:
        item = _base_service(db).create_knowledge_base(current_user.id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return item


@router.get("/{knowledge_base_id}", response_model=KnowledgeBaseResponse)
def get_knowledge_base(
    knowledge_base_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> KnowledgeBaseResponse:
    item = _base_service(db).get_knowledge_base(knowledge_base_id, current_user.id)
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found")
    return item


@router.patch("/{knowledge_base_id}", response_model=KnowledgeBaseResponse)
def update_knowledge_base(
    knowledge_base_id: str,
    payload: KnowledgeBaseUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> KnowledgeBaseResponse:
    try:
        item = _base_service(db).update_knowledge_base(knowledge_base_id, current_user.id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found")
    return item


@router.delete("/{knowledge_base_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_knowledge_base(
    knowledge_base_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    deleted = _base_service(db).delete_knowledge_base(knowledge_base_id, current_user.id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found")


@router.get("/{knowledge_base_id}/documents", response_model=list[KnowledgeDocumentResponse])
def list_documents(
    knowledge_base_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[KnowledgeDocumentResponse]:
    items = _document_service(db).list_documents(knowledge_base_id, current_user.id)
    if items is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found")
    return items


@router.post(
    "/{knowledge_base_id}/documents",
    response_model=KnowledgeDocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_document(
    knowledge_base_id: str,
    payload: KnowledgeDocumentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> KnowledgeDocumentResponse:
    try:
        item = _document_service(db).add_document(knowledge_base_id, current_user.id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found")
    return item


@router.delete("/{knowledge_base_id}/documents/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    knowledge_base_id: str,
    document_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    deleted = _document_service(db).delete_document(knowledge_base_id, document_id, current_user.id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge document not found")


@router.post("/{knowledge_base_id}/documents/{document_id}/parse", response_model=KnowledgeDocumentParseResponse)
def parse_document(
    knowledge_base_id: str,
    document_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> KnowledgeDocumentParseResponse:
    result = _document_service(db).parse_document(knowledge_base_id, document_id, current_user.id)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge document not found")
    return result


@router.get(
    "/{knowledge_base_id}/documents/{document_id}/markdown-preview",
    response_model=KnowledgeMarkdownPreviewResponse,
)
def preview_document_markdown(
    knowledge_base_id: str,
    document_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> KnowledgeMarkdownPreviewResponse:
    try:
        result = _document_service(db).preview_markdown(knowledge_base_id, document_id, current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge document not found")
    return result


@router.get("/{knowledge_base_id}/jobs", response_model=list[KnowledgeJobResponse])
def list_jobs(
    knowledge_base_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[KnowledgeJobResponse]:
    items = _job_service(db).list_jobs(knowledge_base_id, current_user.id)
    if items is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found")
    return items


@credential_router.get("/credentials/mineru", response_model=KnowledgeCredentialResponse)
def get_mineru_credential(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> KnowledgeCredentialResponse:
    return _credential_service(db).get_mineru_credential(current_user.id)


@credential_router.patch("/credentials/mineru", response_model=KnowledgeCredentialResponse)
def update_mineru_credential(
    payload: KnowledgeCredentialUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> KnowledgeCredentialResponse:
    return _credential_service(db).update_mineru_credential(current_user.id, payload)


@credential_router.post("/credentials/mineru/test", response_model=KnowledgeConnectionTestResponse)
def test_mineru_credential(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> KnowledgeConnectionTestResponse:
    return _credential_service(db).test_mineru_credential(current_user.id)
