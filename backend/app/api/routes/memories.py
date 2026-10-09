from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.memory_repo import UserMemoryRepository
from app.repositories.message_repo import MessageRepository
from app.repositories.setting_repo import UserSettingRepository
from app.schemas.memory import (
    MemoryExtractionJobResponse,
    MemoryActivityResponse,
    MemoryReviewRequest,
    UserMemoryCreate,
    UserMemoryResponse,
    UserMemoryUpdate,
)
from app.schemas.memory import MemorySuggestRequest, MemorySuggestResponse
from app.services.chat_provider_service import ChatProviderService, resolve_provider_base_url
from app.services.memory_service import MemoryService
from app.services.memory_activity_service import MemoryActivityService
from app.services.memory_candidate_runtime import MemoryExtractionJobService, _source_text
from app.services.memory_extraction_policy import extraction_prompt, verify_evidence
from app.repositories.memory_job_repo import MemoryExtractionJobRepository
from app.services.setting_service import SettingService

router = APIRouter(prefix="/memories", tags=["memories"])


def _build_recent_messages_text(messages: list[object], *, max_chars: int = 12000) -> str:
    return _source_text(messages[-24:], max_chars=max_chars)


def _build_source_message_ids(messages: list[object]) -> str:
    ids: list[str] = []
    for message in messages[-24:]:
        role = getattr(message, "role", "")
        if role != "user":
            continue
        message_id = getattr(message, "id", None)
        if message_id:
            ids.append(str(message_id))
    return ",".join(ids)


def _build_suggestion_prompt(
    *,
    recent_messages_text: str,
    existing_memory_text: str,
    max_candidates: int,
) -> list[dict[str, str]]:
    return extraction_prompt(recent_text=recent_messages_text, existing_text=existing_memory_text,
                             max_candidates=max_candidates)


@router.get("", response_model=list[UserMemoryResponse])
def list_memories(
    memory_status: str | None = Query(default=None, alias="status"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[UserMemoryResponse]:
    service = MemoryService(UserMemoryRepository(db), ConversationRepository(db))
    if memory_status:
        allowed = {"pending", "active", "rejected", "superseded", "expired", "revoked"}
        if memory_status not in allowed:
            raise HTTPException(status_code=400, detail="Invalid memory status")
        return [
            service._memory_response(item, current_user.id)
            for item in UserMemoryRepository(db).list_by_user_and_status(current_user.id, memory_status)
        ]
    return service.list_memories(current_user.id)


@router.post("", response_model=UserMemoryResponse, status_code=status.HTTP_201_CREATED)
@router.post("/remember", response_model=UserMemoryResponse, status_code=status.HTTP_201_CREATED)
def create_memory(
    payload: UserMemoryCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserMemoryResponse:
    service = MemoryService(UserMemoryRepository(db), ConversationRepository(db))
    try:
        return service.create_memory(current_user.id, payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post(
    "/extraction-jobs/{conversation_id}",
    response_model=MemoryExtractionJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def enqueue_extraction_job(
    conversation_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MemoryExtractionJobResponse:
    if not ConversationRepository(db).get_by_user(conversation_id, current_user.id):
        raise HTTPException(status_code=404, detail="Conversation not found")
    messages = MessageRepository(db).list_by_conversation(conversation_id)
    assistant = next(
        (message for message in reversed(messages) if getattr(message, "role", None) == "assistant"
         and message.status == "done"),
        None,
    )
    if not assistant:
        raise HTTPException(status_code=400, detail="Conversation has no completed assistant turn")
    job = MemoryExtractionJobService(db).enqueue_after_turn(
        user_id=current_user.id,
        conversation_id=conversation_id,
        assistant_message_id=assistant.id,
        force=True,
    )
    if not job:
        raise HTTPException(status_code=409, detail="没有新的可提取用户消息")
    return MemoryExtractionJobResponse.model_validate(job)


@router.get("/extraction-jobs", response_model=list[MemoryExtractionJobResponse])
def list_extraction_jobs(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[MemoryExtractionJobResponse]:
    return [
        MemoryExtractionJobResponse.model_validate(job)
        for job in MemoryExtractionJobRepository(db).list_by_user(current_user.id)
    ]


@router.get("/activity/{conversation_id}", response_model=MemoryActivityResponse)
def get_memory_activity(
    conversation_id: str,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MemoryActivityResponse:
    response.headers["Cache-Control"] = "no-store"
    try:
        return MemoryActivityService(db).get_activity(current_user.id, conversation_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Conversation not found") from exc


@router.post("/{memory_id}/approve", response_model=UserMemoryResponse)
def approve_memory_candidate(
    memory_id: str,
    payload: MemoryReviewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserMemoryResponse:
    repo = UserMemoryRepository(db)
    memory = repo.get_by_user(memory_id, current_user.id)
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    try:
        return MemoryService(repo, ConversationRepository(db)).approve_candidate(
            memory=memory,
            expires_at=payload.expires_at,
            supersedes_memory_id=payload.supersedes_memory_id,
            expected_version=payload.expected_version,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{memory_id}/reject", response_model=UserMemoryResponse)
def reject_memory_candidate(
    memory_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserMemoryResponse:
    repo = UserMemoryRepository(db)
    memory = repo.get_by_user(memory_id, current_user.id)
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    try:
        return MemoryService(repo, ConversationRepository(db)).reject_candidate(memory=memory)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/{memory_id}/revoke", response_model=UserMemoryResponse)
@router.post("/{memory_id}/forget", response_model=UserMemoryResponse)
def revoke_memory(
    memory_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserMemoryResponse:
    repo = UserMemoryRepository(db)
    memory = repo.get_by_user(memory_id, current_user.id)
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    try:
        return MemoryService(repo, ConversationRepository(db)).revoke_memory(memory=memory)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/suggest", response_model=MemorySuggestResponse)
async def suggest_memories(
    payload: MemorySuggestRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MemorySuggestResponse:
    conversation = ConversationRepository(db).get_by_user(payload.conversation_id, current_user.id)
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    messages = MessageRepository(db).list_by_conversation(conversation.id)
    recent_messages_text = _build_recent_messages_text(messages)
    source_message_ids = _build_source_message_ids(messages)
    if not recent_messages_text:
        return MemorySuggestResponse(suggestions=[])

    memory_service = MemoryService(UserMemoryRepository(db), ConversationRepository(db))
    setting_service = SettingService(UserSettingRepository(db))
    settings = setting_service.get_or_create_user_settings(current_user.id)
    provider_type = settings.provider_type or "ollama"
    base_url = resolve_provider_base_url(
        provider_type=provider_type,
        configured_ollama_base_url=settings.ollama_base_url,
        configured_api_base_url=getattr(settings, "api_base_url", None),
    )

    try:
        raw = await ChatProviderService().complete_chat(
            provider_type=provider_type,
            base_url=base_url,
            api_key=setting_service.resolve_provider_api_key(current_user.id),
            model_name=settings.default_model,
            messages=_build_suggestion_prompt(
                recent_messages_text=recent_messages_text,
                existing_memory_text=memory_service.build_existing_memory_text(current_user.id, project_id=conversation.project_id),
                max_candidates=payload.max_candidates,
            ),
            temperature=0.1,
            top_p=0.8,
            max_tokens=1600,
        )
    except Exception as exc:
        # Provider/SDK 异常可能包含 URL、响应正文或鉴权信息，不回显原始异常。
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="生成建议记忆失败，请检查模型配置或稍后重试。",
        ) from exc

    suggestions = MemoryService.parse_suggestion_json(
        raw,
        max_candidates=payload.max_candidates,
        source_conversation_id=conversation.id,
        source_message_ids=source_message_ids,
    )
    suggestions = memory_service.enrich_suggestion_risks(
        suggestions=[verified.model_copy(update={"project_id": conversation.project_id})
                     for item in suggestions if (verified := verify_evidence(item, messages[-24:]))],
        existing_memories=[item for item in UserMemoryRepository(db).list_by_user(current_user.id)
                           if memory_service._memory_scope(item, current_user.id) == conversation.project_id],
        scoped=True,
    )
    return MemorySuggestResponse(suggestions=suggestions)


@router.patch("/{memory_id}", response_model=UserMemoryResponse)
def update_memory(
    memory_id: str,
    payload: UserMemoryUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserMemoryResponse:
    repo = UserMemoryRepository(db)
    memory = repo.get_by_user(memory_id, current_user.id)
    if not memory:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found")

    service = MemoryService(repo, ConversationRepository(db))
    try:
        return service.update_memory(memory=memory, payload=payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_memory(
    memory_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    repo = UserMemoryRepository(db)
    memory = repo.get_by_user(memory_id, current_user.id)
    if not memory:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found")

    try:
        repo.delete(memory)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
