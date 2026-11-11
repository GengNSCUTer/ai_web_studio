"""Chat任务资源与只读事件订阅；断开订阅绝不发送取消命令。"""

import asyncio
import json
import time

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from app.api.deps import get_current_user, get_db
from app.core.database import SessionLocal
from app.models.chat_run import ChatRun, ChatRunEvent
from app.models.conversation import Conversation
from app.schemas.message import ChatStreamRequest, ChatRegenerateRequest, ChatEditLastUserRequest
from app.services.chat_run_service import ChatRunService, TERMINAL
from app.api.resource_limits import chat_subscription_capacity
from app.services.chat_sse import encode_chat_sse

router = APIRouter(prefix="/chat/runs", tags=["chat-runs"])


def create_run(db, user, payload, kind="new"):
    run = ChatRunService(db).create(user=user, payload=payload, request_id=payload.client_request_id, kind=kind)
    return ChatRunService.snapshot(run)


@router.post("", status_code=202)
def create_chat_run(payload: ChatStreamRequest, db=Depends(get_db), user=Depends(get_current_user)):
    return create_run(db, user, payload)


@router.post("/regenerate", status_code=202)
def regenerate_chat_run(payload: ChatRegenerateRequest, db=Depends(get_db), user=Depends(get_current_user)):
    return create_run(db, user, payload, "regenerate")


@router.post("/edit", status_code=202)
def edit_chat_run(payload: ChatEditLastUserRequest, db=Depends(get_db), user=Depends(get_current_user)):
    return create_run(db, user, payload, "edit")


@router.get("")
def conversation_runs(conversation_id: str, db=Depends(get_db), user=Depends(get_current_user)):
    conversation = db.scalar(select(Conversation).where(Conversation.id == conversation_id, Conversation.user_id == user.id))
    if not conversation:
        raise HTTPException(404, "会话不存在")
    rows = db.scalars(select(ChatRun).where(ChatRun.conversation_id == conversation_id,
        ChatRun.user_id == user.id).order_by(ChatRun.created_at.desc()).limit(1)).all()
    return [ChatRunService.snapshot(row) for row in rows]


@router.get("/{run_id}")
def get_chat_run(run_id: str, db=Depends(get_db), user=Depends(get_current_user)):
    return ChatRunService.snapshot(ChatRunService(db).owned(run_id, user.id))


@router.post("/{run_id}/cancel")
def cancel_chat_run(run_id: str, db=Depends(get_db), user=Depends(get_current_user)):
    return ChatRunService(db).cancel(run_id, user.id)


def _page(run_id, user_id, after, sessions):
    # 一次短查询结束即释放连接，绝不跨SSE等待持有事务。
    with sessions() as db:
        run = ChatRunService(db).owned(run_id, user_id)
        snapshot = ChatRunService.snapshot(run)
        rows = db.scalars(select(ChatRunEvent).where(ChatRunEvent.run_id == run_id,
            ChatRunEvent.sequence > after).order_by(ChatRunEvent.sequence).limit(256)).all()
        return snapshot, [(row.sequence, json.loads(row.event_json)) for row in rows]


async def subscribe_events(run_id, user_id, *, after=0, sessions=SessionLocal):
    last_heartbeat = time.monotonic()
    while True:
        snapshot, rows = await asyncio.to_thread(_page, run_id, user_id, after, sessions)
        if after < snapshot["events_pruned_through"]:
            after = snapshot["last_event_id"]
            rows = [(after, {"type": "run_snapshot", "run": snapshot})]
            if snapshot["status"] in TERMINAL:
                rows.append((after, {"type": "done" if snapshot["status"] == "succeeded" else "run_status",
                    "status": snapshot["status"], "assistant_message_id": snapshot["assistant_message_id"]}))
        elif not rows and snapshot["status"] in TERMINAL:
            # 重连游标已到末尾时仍返回终态快照，不能让客户端把正常结束当作再次断流。
            rows = [(snapshot["last_event_id"], {"type": "run_snapshot", "run": snapshot})]
        for sequence, event in rows:
            after = sequence
            yield encode_chat_sse({**event, "event_id": sequence, "run_id": run_id},
                event_id=f"{run_id}:{sequence}")
        if after >= snapshot["last_event_id"] and snapshot["status"] in TERMINAL:
            return
        if time.monotonic() - last_heartbeat > 15:
            yield ": heartbeat\n\n"
            last_heartbeat = time.monotonic()
        await asyncio.sleep(.25)


@router.get("/{run_id}/events", dependencies=[Depends(chat_subscription_capacity, scope="request")])
async def chat_run_events(run_id: str, after: int = Query(default=0, ge=0),
    format: str = Query(default="sse", pattern="^sse$"),
    last_event_id: str | None = Header(default=None), db=Depends(get_db), user=Depends(get_current_user)):
    snapshot = ChatRunService.snapshot(ChatRunService(db).owned(run_id, user.id))
    user_id = str(user.id)
    sessions = sessionmaker(bind=db.get_bind(), autoflush=False)
    db.close()
    if last_event_id:
        try:
            event_run, sequence = last_event_id.rsplit(":", 1)
            if event_run != run_id:
                raise ValueError()
            sequence = int(sequence)
            if sequence < 0:
                raise ValueError()
            after = max(after, sequence)
        except ValueError as exc:
            raise HTTPException(400, "事件游标不属于当前任务") from exc
    if after > snapshot["last_event_id"] or after < 0:
        raise HTTPException(400, "事件游标超出范围")
    return StreamingResponse(subscribe_events(run_id, user_id, after=after, sessions=sessions),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no",
            "x-chat-run-id": run_id, "x-conversation-id": snapshot["conversation_id"],
            "x-assistant-message-id": snapshot["assistant_message_id"]})
