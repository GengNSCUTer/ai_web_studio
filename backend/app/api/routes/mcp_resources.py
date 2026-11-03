"""MCP Resources的显式浏览、文本预览入口；不执行本机URI。"""

import asyncio
from fastapi import APIRouter, Depends, HTTPException

from app.api.deps import get_current_user
from app.models.user import User
from app.schemas.mcp_resource import McpResourceSelection
from app.services.mcp_resource_service import McpResourceService


router = APIRouter(prefix="/mcp-resources")


@router.get("/servers")
async def servers(project_id: str | None = None, current_user: User = Depends(get_current_user)):
    return await asyncio.to_thread(McpResourceService(user_id=current_user.id, project_id=project_id).servers)


@router.get("/servers/{server_id}")
async def list_resources(server_id: str, project_id: str | None = None,
                         current_user: User = Depends(get_current_user)):
    try:
        return await McpResourceService(user_id=current_user.id, project_id=project_id).list(server_id)
    except RuntimeError:
        raise HTTPException(502, "MCP资源发现失败，请检查服务地址、凭据和协议支持。") from None


@router.post("/read")
async def read_resource(payload: McpResourceSelection, project_id: str | None = None,
                        current_user: User = Depends(get_current_user)):
    try:
        source = await McpResourceService(user_id=current_user.id, project_id=project_id).read(payload.server_id, payload.uri)
        return source.to_public_dict()
    except RuntimeError:
        raise HTTPException(502, "MCP资源读取失败，正文未进入会话。") from None
