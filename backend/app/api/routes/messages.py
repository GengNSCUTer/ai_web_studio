from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.repositories.attachment_repo import AttachmentRepository
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.message_repo import MessageRepository
from app.repositories.tool_trace_repo import ToolTraceRepository
from app.schemas.message import MessageBulkDeleteRequest, MessageCreate, MessageResponse
from app.services.message_service import MessageService

router = APIRouter(prefix="/conversations/{conversation_id}/messages", tags=["messages"])


@router.get("", response_model=list[MessageResponse])
def list_messages(
    conversation_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[MessageResponse]:
    # Message 没有 user_id，因此每个消息接口的第一步都必须校验 conversation 属于 current_user。
    conversation = ConversationRepository(db).get_by_user(conversation_id, current_user.id)
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    service = MessageService(MessageRepository(db), AttachmentRepository(db))
    messages = service.list_messages(conversation_id)
    events_by_message_id = ToolTraceRepository(db).get_events_by_assistant_messages(
        [message.id for message in messages if message.role == "assistant"]
    )
    responses: list[MessageResponse] = []
    for message in messages:
        item = MessageResponse.model_validate(message)
        item.tool_events = events_by_message_id.get(message.id, [])
        responses.append(item)
    return responses


@router.post("", response_model=MessageResponse, status_code=status.HTTP_201_CREATED)
def create_message(
    conversation_id: str,
    payload: MessageCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> MessageResponse:
    conversation_repo = ConversationRepository(db)
    # 公开创建消息入口只允许给自己的会话追加 user 消息；MessageCreate schema 会拒绝 assistant/system。
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
    # 先校验会话归属，再按 conversation_id + message_id 删除，形成双重边界。
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
    # 批量删除同样先收口到用户自己的会话；传入其他会话 message_id 会在 repo 层被过滤掉。
    conversation = conversation_repo.get_by_user(conversation_id, current_user.id)
    if not conversation:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    service = MessageService(MessageRepository(db), AttachmentRepository(db))
    deleted_count = service.bulk_delete_messages(conversation_id, payload.message_ids)
    conversation_repo.touch(conversation_id)
    return {"deleted_count": deleted_count}
