from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.repositories.knowledge_repo import (
    KnowledgeBaseRepository,
    KnowledgeChunkRepository,
    KnowledgeDocumentRepository,
    KnowledgeEvalCaseRepository,
    KnowledgeEvalResultRepository,
    KnowledgeEvalRunRepository,
    KnowledgeEvalSetRepository,
    KnowledgeJobRepository,
    KnowledgeRetrievalLogRepository,
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
    KnowledgeDocumentIndexResponse,
    KnowledgeDocumentParseResponse,
    KnowledgeDocumentResponse,
    KnowledgeEvalCaseCreate,
    KnowledgeEvalCaseResponse,
    KnowledgeEvalMatrixRequest,
    KnowledgeEvalMatrixResponse,
    KnowledgeEvalOutcomeResponse,
    KnowledgeEvalRunRequest,
    KnowledgeEvalRunResponse,
    KnowledgeEvalSetCreate,
    KnowledgeEvalSetResponse,
    KnowledgeJobResponse,
    KnowledgeMarkdownPreviewResponse,
    KnowledgeRetrievalLogResponse,
    KnowledgeRetrievalTestRequest,
    KnowledgeRetrievalTestResponse,
)
from app.services.knowledge_service import (
    KnowledgeBaseService,
    KnowledgeCredentialService,
    KnowledgeDocumentConflictError,
    KnowledgeDocumentService,
    KnowledgeJobService,
)
from app.repositories.setting_repo import UserSettingRepository
from app.services.knowledge_evaluation_service import KnowledgeEvaluationService
from app.services.setting_service import SettingService

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


def _retrieval_log_response(log: object) -> KnowledgeRetrievalLogResponse:
    return KnowledgeRetrievalLogResponse(**KnowledgeRetrievalLogRepository.to_public_dict(log))


def _evaluation_service(db: Session) -> KnowledgeEvaluationService:
    return KnowledgeEvaluationService(
        base_repo=KnowledgeBaseRepository(db),
        chunk_repo=KnowledgeChunkRepository(db),
        eval_set_repo=KnowledgeEvalSetRepository(db),
        eval_case_repo=KnowledgeEvalCaseRepository(db),
        eval_run_repo=KnowledgeEvalRunRepository(db),
        eval_result_repo=KnowledgeEvalResultRepository(db),
        setting_service=SettingService(UserSettingRepository(db)),
    )


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
    try:
        deleted = _document_service(db).delete_document(knowledge_base_id, document_id, current_user.id)
    except KnowledgeDocumentConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge document not found")


@router.post("/{knowledge_base_id}/documents/{document_id}/parse", response_model=KnowledgeDocumentParseResponse)
def parse_document(
    knowledge_base_id: str,
    document_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> KnowledgeDocumentParseResponse:
    result = _document_service(db).enqueue_parse_document(knowledge_base_id, document_id, current_user.id)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge document not found")
    return result


@router.post("/{knowledge_base_id}/documents/{document_id}/index", response_model=KnowledgeDocumentIndexResponse)
def index_document(
    knowledge_base_id: str,
    document_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> KnowledgeDocumentIndexResponse:
    try:
        result = _document_service(db).enqueue_index_document(knowledge_base_id, document_id, current_user.id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
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


@router.post("/{knowledge_base_id}/retrieval-test", response_model=KnowledgeRetrievalTestResponse)
def test_retrieval(
    knowledge_base_id: str,
    payload: KnowledgeRetrievalTestRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> KnowledgeRetrievalTestResponse:
    try:
        result = _document_service(db).test_retrieval(
            knowledge_base_id=knowledge_base_id,
            user_id=current_user.id,
            query=payload.query,
            top_k=payload.top_k,
            document_ids=payload.document_ids,
            file_types=payload.file_types,
            page_start=payload.page_start,
            page_end=payload.page_end,
            section_query=payload.section_query,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found")
    return result


@router.get("/{knowledge_base_id}/retrieval-logs", response_model=list[KnowledgeRetrievalLogResponse])
def list_retrieval_logs(
    knowledge_base_id: str,
    limit: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[KnowledgeRetrievalLogResponse]:
    if not KnowledgeBaseRepository(db).get_by_user(knowledge_base_id, current_user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found")
    logs = KnowledgeRetrievalLogRepository(db).list_by_knowledge_base(
        knowledge_base_id=knowledge_base_id,
        user_id=current_user.id,
        limit=limit,
    )
    return [_retrieval_log_response(log) for log in logs]


@credential_router.get("/retrieval-logs/{log_id}", response_model=KnowledgeRetrievalLogResponse)
def get_retrieval_log(
    log_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> KnowledgeRetrievalLogResponse:
    log = KnowledgeRetrievalLogRepository(db).get_by_user(log_id, current_user.id)
    if not log:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge retrieval log not found")
    return _retrieval_log_response(log)


@router.get("/{knowledge_base_id}/eval-sets", response_model=list[KnowledgeEvalSetResponse])
def list_eval_sets(
    knowledge_base_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[KnowledgeEvalSetResponse]:
    if not KnowledgeBaseRepository(db).get_by_user(knowledge_base_id, current_user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found")
    return _evaluation_service(db).list_eval_sets(knowledge_base_id, current_user.id)


@router.post(
    "/{knowledge_base_id}/eval-sets",
    response_model=KnowledgeEvalSetResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_eval_set(
    knowledge_base_id: str,
    payload: KnowledgeEvalSetCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> KnowledgeEvalSetResponse:
    result = _evaluation_service(db).create_eval_set(knowledge_base_id, current_user.id, payload)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Knowledge base not found")
    return result


@router.get("/{knowledge_base_id}/eval-sets/{eval_set_id}/cases", response_model=list[KnowledgeEvalCaseResponse])
def list_eval_cases(
    knowledge_base_id: str,
    eval_set_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[KnowledgeEvalCaseResponse]:
    service = _evaluation_service(db)
    eval_set = KnowledgeEvalSetRepository(db).get_by_user(eval_set_id, current_user.id)
    if not eval_set or eval_set.knowledge_base_id != knowledge_base_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Evaluation set not found")
    return service.list_eval_cases(eval_set_id, current_user.id)


@router.post(
    "/{knowledge_base_id}/eval-sets/{eval_set_id}/cases",
    response_model=KnowledgeEvalCaseResponse,
    status_code=status.HTTP_201_CREATED,
)
def add_eval_case(
    knowledge_base_id: str,
    eval_set_id: str,
    payload: KnowledgeEvalCaseCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> KnowledgeEvalCaseResponse:
    try:
        result = _evaluation_service(db).add_eval_case(knowledge_base_id, eval_set_id, current_user.id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Evaluation set not found")
    return result


@router.get("/{knowledge_base_id}/eval-sets/{eval_set_id}/runs", response_model=list[KnowledgeEvalRunResponse])
def list_eval_runs(
    knowledge_base_id: str,
    eval_set_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[KnowledgeEvalRunResponse]:
    eval_set = KnowledgeEvalSetRepository(db).get_by_user(eval_set_id, current_user.id)
    if not eval_set or eval_set.knowledge_base_id != knowledge_base_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Evaluation set not found")
    return _evaluation_service(db).list_eval_runs(knowledge_base_id, eval_set_id, current_user.id)


@router.post("/{knowledge_base_id}/eval-sets/{eval_set_id}/runs", response_model=KnowledgeEvalOutcomeResponse)
def run_eval_set(
    knowledge_base_id: str,
    eval_set_id: str,
    payload: KnowledgeEvalRunRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> KnowledgeEvalOutcomeResponse:
    try:
        outcome = _evaluation_service(db).run_eval(knowledge_base_id, eval_set_id, current_user.id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not outcome:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Evaluation set not found")
    return KnowledgeEvalOutcomeResponse(run=outcome.run, results=outcome.results)


@router.post(
    "/{knowledge_base_id}/eval-sets/{eval_set_id}/matrix-runs",
    response_model=KnowledgeEvalMatrixResponse,
)
def run_eval_matrix(
    knowledge_base_id: str,
    eval_set_id: str,
    payload: KnowledgeEvalMatrixRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> KnowledgeEvalMatrixResponse:
    """Run the fixed Vector/BM25/Hybrid/Rerank comparison over one Gold Set."""

    try:
        outcome = _evaluation_service(db).run_eval_matrix(
            knowledge_base_id,
            eval_set_id,
            current_user.id,
            top_k=payload.top_k,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not outcome:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Evaluation set not found")
    return KnowledgeEvalMatrixResponse(
        eval_set_id=outcome.eval_set_id,
        runs=outcome.runs,
        comparison=outcome.comparison,
    )


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
