"""把Chat执行权传给子图/Executor；正文或Planner不能建立这个上下文。"""

import asyncio
from contextvars import ContextVar

current_chat_guard = ContextVar("current_chat_guard", default=None)


async def check_chat_execution():
    check = current_chat_guard.get()
    if check is not None:
        await asyncio.to_thread(check)
