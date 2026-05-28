from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.tool_config import UserToolCredential, WorkspaceToolSetting
from app.models.user import User
from app.repositories.project_repo import ProjectRepository
from app.repositories.tool_config_repo import ToolConfigRepository
from app.schemas.tool_config import (
    ToolConnectionTestResponse,
    ToolDefinitionResponse,
    ToolSettingsResponse,
    UserToolCredentialResponse,
    UserToolCredentialUpdate,
    WorkspaceToolSettingResponse,
    WorkspaceToolSettingUpdate,
)
from app.services.tools.credentials import ToolCredentialResolver
from app.services.tools.providers.amap import AmapToolProvider
from app.services.tools.providers.tavily import TavilySearchProvider
from app.services.tools.registry import ToolRegistry

router = APIRouter(prefix="/tools", tags=["tools"])


def _mask_secret(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 8:
        return "****"
    return f"{value[:4]}****{value[-4:]}"


def _credential_response(
    *,
    provider_key: str,
    credential: UserToolCredential | None,
    resolver: ToolCredentialResolver,
    user_id: str,
) -> UserToolCredentialResponse:
    resolved = resolver.resolve(user_id=user_id, provider_key=provider_key)
    if credential:
        saved_key = (credential.api_key or "").strip() or None
        return UserToolCredentialResponse(
            provider_key=provider_key,
            credential_name=credential.credential_name,
            is_enabled=credential.is_enabled,
            has_api_key=bool(saved_key or resolved.api_key),
            api_key_masked=_mask_secret(saved_key or resolved.api_key),
            source="user" if saved_key else resolved.source,
        )
    return UserToolCredentialResponse(
        provider_key=provider_key,
        credential_name="环境变量 fallback",
        is_enabled=resolved.is_enabled,
        has_api_key=bool(resolved.api_key),
        api_key_masked=_mask_secret(resolved.api_key),
        source=resolved.source,
    )


@router.get("/settings", response_model=ToolSettingsResponse)
def get_tool_settings(
    project_id: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ToolSettingsResponse:
    if project_id and not ProjectRepository(db).get_by_user(project_id, current_user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    registry = ToolRegistry()
    repo = ToolConfigRepository(db)
    resolver = ToolCredentialResolver(db)
    credentials = {item.provider_key: item for item in repo.list_credentials(current_user.id)}
    provider_keys = sorted({tool.provider for tool in registry.list_definitions()})
    workspace_settings = repo.list_workspace_settings(project_id) if project_id else []

    return ToolSettingsResponse(
        tools=[
            ToolDefinitionResponse(
                tool_key=tool.tool_key,
                provider=tool.provider,
                category=tool.category,
                display_name=tool.display_name,
                description=tool.description,
                read_only=tool.read_only,
                enabled_by_default=tool.enabled_by_default,
                credential_required=True,
            )
            for tool in registry.list_definitions()
        ],
        credentials=[
            _credential_response(
                provider_key=provider_key,
                credential=credentials.get(provider_key),
                resolver=resolver,
                user_id=current_user.id,
            )
            for provider_key in provider_keys
        ],
        workspace_settings=[
            WorkspaceToolSettingResponse(
                project_id=item.project_id,
                tool_key=item.tool_key,
                is_enabled=item.is_enabled,
            )
            for item in workspace_settings
        ],
    )


@router.patch("/credentials/{provider_key}", response_model=UserToolCredentialResponse)
def update_tool_credential(
    provider_key: str,
    payload: UserToolCredentialUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserToolCredentialResponse:
    valid_providers = {tool.provider for tool in ToolRegistry().list_definitions()}
    if provider_key not in valid_providers:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool provider not found")

    repo = ToolConfigRepository(db)
    credential = repo.get_credential(current_user.id, provider_key)
    if not credential:
        credential = UserToolCredential(
            user_id=current_user.id,
            provider_key=provider_key,
            credential_name=f"{provider_key} 默认凭证",
            api_key=None,
            is_enabled=True,
        )

    data = payload.model_dump(exclude_unset=True)
    if "credential_name" in data and data["credential_name"] is not None:
        credential.credential_name = data["credential_name"].strip() or f"{provider_key} 默认凭证"
    if "api_key" in data:
        credential.api_key = (data["api_key"] or "").strip() or None
    if "is_enabled" in data and data["is_enabled"] is not None:
        credential.is_enabled = bool(data["is_enabled"])

    saved = repo.save_credential(credential)
    return UserToolCredentialResponse(
        provider_key=provider_key,
        credential_name=saved.credential_name,
        is_enabled=saved.is_enabled,
        has_api_key=bool((saved.api_key or "").strip()),
        api_key_masked=_mask_secret(saved.api_key),
        source="user",
    )


@router.patch("/workspaces/{project_id}/{tool_key}", response_model=WorkspaceToolSettingResponse)
def update_workspace_tool_setting(
    project_id: str,
    tool_key: str,
    payload: WorkspaceToolSettingUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> WorkspaceToolSettingResponse:
    if tool_key not in {tool.tool_key for tool in ToolRegistry().list_definitions()}:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool not found")
    if not ProjectRepository(db).get_by_user(project_id, current_user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    repo = ToolConfigRepository(db)
    setting = repo.get_workspace_setting(project_id, tool_key)
    if not setting:
        setting = WorkspaceToolSetting(project_id=project_id, tool_key=tool_key, is_enabled=payload.is_enabled)
    else:
        setting.is_enabled = payload.is_enabled
    saved = repo.save_workspace_setting(setting)
    return WorkspaceToolSettingResponse(
        project_id=saved.project_id,
        tool_key=saved.tool_key,
        is_enabled=saved.is_enabled,
    )


@router.post("/credentials/{provider_key}/test", response_model=ToolConnectionTestResponse)
async def test_tool_credential(
    provider_key: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ToolConnectionTestResponse:
    credential = ToolCredentialResolver(db).resolve(user_id=current_user.id, provider_key=provider_key)
    if not credential.is_enabled or not credential.api_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="工具未启用或未配置 API Key")

    try:
        if provider_key == "tavily":
            sources = await TavilySearchProvider().query("AI news", api_key=credential.api_key)
        elif provider_key == "amap":
            sources = await AmapToolProvider().query_weather("广州天气", api_key=credential.api_key)
        else:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool provider not found")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"连接失败：{exc}") from exc

    return ToolConnectionTestResponse(
        ok=True,
        provider_key=provider_key,
        message=f"连接成功，返回 {len(sources)} 个来源，凭证来源：{credential.source}",
    )
