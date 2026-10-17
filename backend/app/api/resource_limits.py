"""依赖的清理范围覆盖整个响应流，而不只覆盖路由函数返回前。"""

from fastapi import Depends
from app.api.deps import get_current_user
from app.core.config import settings
from app.services.resource_quota_service import resource_capacity


async def chat_capacity(user=Depends(get_current_user)):
    async with resource_capacity(str(user.id), "chat", settings.user_chat_concurrency) as token:
        yield token


async def upload_capacity(user=Depends(get_current_user)):
    async with resource_capacity(str(user.id), "upload", settings.user_upload_concurrency) as token:
        yield token
