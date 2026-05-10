from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.repositories.attachment_repo import AttachmentRepository
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.message_repo import MessageRepository
from app.schemas.message import MessageBulkDeleteRequest, MessageCreate, MessageResponse
from app.services.message_service import MessageService

router = APIRouter(prefix="/conversations/{conversation_id}/messages", tags=["messages"])


@router.get("", response_model=list[MessageResponse])
def list_messages(
    conversation_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[MessageResponse]:
    conversation = ConversationRepository(db).get_by_user(conversation_id, current_user.id)
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    service = MessageService(MessageRepository(db), AttachmentRepository(db))
    return service.list_messages(conversation_id)


@router.post("", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
def create_message(
    conversation_id: str,
    payload: MessageCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MessageResponse:
    conversation_repo = ConversationRepository(db)
    conversation = conversation_repo.get_by_user(conversation_id, current_user.id)
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    service = MessageService(MessageRepository(db), AttachmentRepository(db))
    created = service.create_message(conversation_id, payload)
    conversation_repo.touch(conversation_id)
    return created


@router.delete("/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_message(
    conversation_id: str,
    message_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    conversation_repo = ConversationRepository(db)
    conversation = conversation_repo.get_by_user(conversation_id, current_user.id)
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    service = MessageService(MessageRepository(db), AttachmentRepository(db))
    deleted = service.delete_message(message_id, conversation_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")

    conversation_repo.touch(conversation_id)


@router.post("/bulk-delete")
def bulk_delete_messages(
    conversation_id: str,
    payload: MessageBulkDeleteRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, int]:
    conversation_repo = ConversationRepository(db)
    conversation = conversation_repo.get_by_user(conversation_id, current_user.id)
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    service = MessageService(MessageRepository(db), AttachmentRepository(db))
    deleted_count = service.bulk_delete_messages(conversation_id, payload.message_ids)
    conversation_repo.touch(conversation_id)
    return {"deleted_count": deleted_count}

