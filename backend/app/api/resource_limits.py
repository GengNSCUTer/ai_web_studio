"""依赖的清理范围覆盖整个响应流，而不只覆盖路由函数返回前。"""

from fastapi import Depends
from app.api.deps import get_current_user, get_db
from app.core.config import settings
from app.services.resource_quota_service import resource_capacity


def _quota_user_id(user=Depends(get_current_user), db=Depends(get_db)) -> str:
    # 鉴权只读事务先归还连接，否则池满时各请求会占着鉴权连接等待额度连接。
    user_id = str(user.id)
    db.close()
    return user_id


async def chat_capacity(user_id=Depends(_quota_user_id)):
    async with resource_capacity(user_id, "chat", settings.user_chat_concurrency) as token:
        yield token


async def chat_subscription_capacity(user_id=Depends(_quota_user_id)):
    # 订阅额度只保护读事件连接；断开后释放它，不释放或取消后台ChatRun。
    async with resource_capacity(user_id, "chat_events", max(4, settings.user_chat_concurrency * 2)) as token:
        yield token


async def upload_capacity(user_id=Depends(_quota_user_id)):
    async with resource_capacity(user_id, "upload", settings.user_upload_concurrency) as token:
        yield token
