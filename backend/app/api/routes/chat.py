"""旧Chat流路径只创建持久化任务并订阅，生成由独立Worker负责。"""

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.api.resource_limits import chat_subscription_capacity
from app.models.user import User
from app.schemas.message import ChatStreamRequest, ChatRegenerateRequest, ChatEditLastUserRequest
# 保留内部导入兼容，实际生成和测试只使用同一份服务实现。
from app.services.chat_stream_service import _build_streaming_response, _persist_stream_result, _stringify_stats

router = APIRouter(prefix="/chat", tags=["chat"],
    dependencies=[Depends(chat_subscription_capacity, scope="request")])


@router.post("/text-stream")
async def chat_text_stream(payload: ChatStreamRequest, db: Session = Depends(get_db),
                           current_user: User = Depends(get_current_user)) -> StreamingResponse:
    from app.api.routes.chat_runs import enqueue_and_subscribe
    return await enqueue_and_subscribe(db, current_user, payload, text_only=True)


@router.post("/events-stream")
async def chat_events_stream(payload: ChatStreamRequest, db: Session = Depends(get_db),
                             current_user: User = Depends(get_current_user)) -> StreamingResponse:
    from app.api.routes.chat_runs import enqueue_and_subscribe
    return await enqueue_and_subscribe(db, current_user, payload)


@router.post("/regenerate-last-stream")
async def regenerate_last_answer_stream(payload: ChatRegenerateRequest, db: Session = Depends(get_db),
                                       current_user: User = Depends(get_current_user)) -> StreamingResponse:
    from app.api.routes.chat_runs import enqueue_and_subscribe
    return await enqueue_and_subscribe(db, current_user, payload, kind="regenerate")


@router.post("/edit-last-user-stream")
async def edit_last_user_stream(payload: ChatEditLastUserRequest, db: Session = Depends(get_db),
                               current_user: User = Depends(get_current_user)) -> StreamingResponse:
    from app.api.routes.chat_runs import enqueue_and_subscribe
    return await enqueue_and_subscribe(db, current_user, payload, kind="edit")
