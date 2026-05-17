from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.message_repo import MessageRepository
from app.repositories.share_repo import ConversationShareRepository
from app.schemas.share import (
    ConversationShareCreate,
    ConversationShareResponse,
    ConversationShareUpdate,
    PublicConversationShareResponse,
)
from app.services.share_service import ConversationShareService

router = APIRouter(tags=["shares"])


def _service(db: Session) -> ConversationShareService:
    return ConversationShareService(
        ConversationShareRepository(db),
        ConversationRepository(db),
        MessageRepository(db),
    )


@router.get("/conversations/{conversation_id}/share", response_model=ConversationShareResponse | None)
def get_conversation_share(
    conversation_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ConversationShareResponse | None:
    return _service(db).get_share(conversation_id, current_user.id)


@router.post("/conversations/{conversation_id}/share", response_model=ConversationShareResponse)
def create_conversation_share(
    conversation_id: str,
    payload: ConversationShareCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ConversationShareResponse:
    share = _service(db).create_or_enable_share(conversation_id, current_user.id, payload)
    if not share:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    return share


@router.patch("/conversations/{conversation_id}/share", response_model=ConversationShareResponse)
def update_conversation_share(
    conversation_id: str,
    payload: ConversationShareUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ConversationShareResponse:
    share = _service(db).update_share(conversation_id, current_user.id, payload)
    if not share:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Share not found")
    return share


@router.delete("/conversations/{conversation_id}/share", status_code=status.HTTP_204_NO_CONTENT)
def revoke_conversation_share(
    conversation_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    revoked = _service(db).revoke_share(conversation_id, current_user.id)
    if not revoked:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Share not found")


@router.get("/shares/{token}", response_model=PublicConversationShareResponse)
def get_public_conversation_share(
    token: str,
    db: Session = Depends(get_db),
) -> PublicConversationShareResponse:
    share = _service(db).get_public_share(token)
    if not share:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Share not found")
    return share
