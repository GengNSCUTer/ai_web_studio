from __future__ import annotations

import json
import re
import hashlib
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.tool_config import (
    McpServer,
    McpTool,
    UserToolCredential,
    WorkspaceAgentPolicy,
    WorkspaceToolSetting,
)
from app.models.user import User
from app.repositories.project_repo import ProjectRepository
from app.repositories.tool_config_repo import ToolConfigRepository
from app.schemas.tool_config import (
    McpServerCreate,
    McpServerResponse,
    McpServerUpdate,
    McpSyncResponse,
    McpToolResponse,
    McpToolTestRequest,
    McpToolUpdate,
    ToolConnectionTestResponse,
    ToolDefinitionResponse,
    ToolSettingsResponse,
    SkillInstallationResponse,
    SkillInstallationUpdate,
    SkillGoldSetAssessmentRequest,
    SkillGoldSetBatchAssessmentRequest,
    SkillRecommendationResponse,
    UserToolCredentialResponse,
    UserToolCredentialUpdate,
    WorkspaceAgentPolicyResponse,
    WorkspaceAgentPolicyUpdate,
    WorkspaceToolSettingResponse,
    WorkspaceToolSettingUpdate,
)
from app.services.tools.credentials import ToolCredentialResolver
from app.services.tools.mcp_client import McpHttpClient
from app.services.tools.mcp_security import (
    McpEndpointPolicyError,
    apply_remote_tool_security_policy,
    enforce_mcp_endpoint_target_policy,
    validate_mcp_endpoint_url,
)
from app.services.tools.providers.amap import AmapToolProvider
from app.services.tools.providers.tavily import TavilySearchProvider
from app.services.tools.catalog import ToolCatalog
from app.services.secret_service import SecretService
from app.services.skill_catalog import SkillCatalog, SkillCatalogError
from app.services.skill_recommendation_service import SkillGoldSetEvaluator, SkillRecommendationService

router = APIRouter(prefix="/tools", tags=["tools"])
secret_service = SecretService()


def _json_dumps(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_loads(value: str | None, fallback: object) -> object:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return fallback


def _dynamic_tool_key(*, server_id: str, raw_name: str) -> str:
    """Create a globally unique, bounded and stable key for a discovered MCP tool."""
    name_slug = _slug(raw_name)[:48] or "tool"
    name_hash = hashlib.sha256(raw_name.encode("utf-8")).hexdigest()[:10]
    return f"mcp.{server_id}.{name_slug}.{name_hash}"


def _slug(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip().lower()).strip("-")
    return normalized or "mcp"


def _dt(value: object) -> str | None:
    return value.isoformat() if hasattr(value, "isoformat") else None


def _mcp_endpoint(server: McpServer, api_key: str | None) -> tuple[str, dict[str, str]]:
    endpoint = server.url.replace("{api_key}", api_key or "")
    if server.auth_type == "bearer" and api_key:
        return endpoint, {"Authorization": f"Bearer {api_key}"}
    if server.auth_type == "api_key_header" and api_key:
        return endpoint, {"X-API-Key": api_key}
    return endpoint, {}


def _server_response(server: McpServer) -> McpServerResponse:
    return McpServerResponse(
        id=server.id,
        server_key=server.server_key,
        name=server.name,
        description=server.description,
        url=server.url,
        transport_type=server.transport_type,
        auth_type=server.auth_type,
        credential_provider=server.credential_provider,
        project_id=server.project_id,
        trust_level=server.trust_level,
        is_enabled=server.is_enabled,
        last_sync_at=_dt(server.last_sync_at),
        last_error=server.last_error,
    )


def _tool_response(tool: McpTool, server: McpServer | None = None) -> McpToolResponse:
    annotations = _json_loads(tool.annotations_json, {})
    remote_read_only_hint = (
        annotations.get("readOnlyHint")
        if isinstance(annotations, dict) and isinstance(annotations.get("readOnlyHint"), bool)
        else None
    )
    return McpToolResponse(
        id=tool.id,
        server_id=tool.server_id,
        server_key=server.server_key if server else None,
        raw_name=tool.raw_name,
        tool_key=tool.tool_key,
        display_name=tool.display_name,
        description=tool.description,
        description_override=tool.description_override,
        input_schema=_json_loads(tool.input_schema_json, {}),
        output_schema=_json_loads(tool.output_schema_json, {}),
        fixed_arguments=_json_loads(tool.fixed_arguments_json, {}),
        category=tool.category,
        risk_level=tool.risk_level,
        read_only=tool.read_only,
        remote_read_only_hint=remote_read_only_hint,
        risk_reviewed=tool.risk_reviewed,
        is_enabled=tool.is_enabled,
        last_seen_at=_dt(tool.last_seen_at),
    )


def _credential_response(
    *,
    provider_key: str,
    credential: UserToolCredential | None,
    resolver: ToolCredentialResolver,
    user_id: str,
) -> UserToolCredentialResponse:
    resolved = resolver.resolve(user_id=user_id, provider_key=provider_key)
    if credential:
        saved_key = secret_service.decrypt(credential.api_key)
        return UserToolCredentialResponse(
            provider_key=provider_key,
            credential_name=credential.credential_name,
            is_enabled=credential.is_enabled,
            has_api_key=bool(saved_key or resolved.api_key),
            api_key_masked=secret_service.mask(saved_key or resolved.api_key),
            source="user" if saved_key else resolved.source,
        )
    return UserToolCredentialResponse(
        provider_key=provider_key,
        credential_name="环境变量 fallback",
        is_enabled=resolved.is_enabled,
        has_api_key=bool(resolved.api_key),
        api_key_masked=secret_service.mask(resolved.api_key),
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

    registry = ToolCatalog(db=db, user_id=current_user.id, project_id=project_id)
    repo = ToolConfigRepository(db)
    resolver = ToolCredentialResolver(db)
    credentials = {item.provider_key: item for item in repo.list_credentials(current_user.id)}
    mcp_servers = repo.list_mcp_servers(current_user.id, project_id=project_id)
    mcp_tool_pairs = repo.list_mcp_tools(user_id=current_user.id, project_id=project_id)
    provider_keys = sorted(
        {tool.credential_provider for tool in registry.list_definitions() if tool.credential_required}
        | {
            server.credential_provider or server.server_key
            for server in mcp_servers
            if server.auth_type != "none" or "{api_key}" in server.url
        }
    )
    workspace_settings = repo.list_workspace_settings(project_id) if project_id else []
    workspace_policy = repo.get_workspace_policy(project_id) if project_id else None

    return ToolSettingsResponse(
        tools=[
            ToolDefinitionResponse(
                tool_key=tool.tool_key,
                provider=tool.provider,
                category=tool.category,
                display_name=tool.display_name,
                description=tool.description,
                source_type=tool.source_type,
                adapter_type=tool.adapter_type,
                risk_level=tool.risk_level,
                input_schema=tool.input_schema,
                read_only=tool.read_only,
                enabled_by_default=tool.enabled_by_default,
                credential_required=tool.credential_required,
                credential_provider=tool.credential_provider,
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
        workspace_policy=(
            WorkspaceAgentPolicyResponse(
                project_id=project_id,
                permission_mode=workspace_policy.permission_mode if workspace_policy else "ask",
            )
            if project_id
            else None
        ),
        mcp_servers=[_server_response(server) for server in mcp_servers],
        mcp_tools=[_tool_response(tool, server) for tool, server in mcp_tool_pairs],
        skills=[
            SkillInstallationResponse(**item)
            for item in SkillCatalog().list_for_user(
                db=db,
                user_id=current_user.id,
                project_id=project_id,
            )
        ],
    )


@router.get("/skills", response_model=list[SkillInstallationResponse])
def list_skills(
    project_id: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[SkillInstallationResponse]:
    if project_id and not ProjectRepository(db).get_by_user(project_id, current_user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return [
        SkillInstallationResponse(**item)
        for item in SkillCatalog().list_for_user(
            db=db,
            user_id=current_user.id,
            project_id=project_id,
        )
    ]


@router.put("/skills/{skill_key}", response_model=SkillInstallationResponse)
def install_or_update_skill(
    skill_key: str,
    payload: SkillInstallationUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SkillInstallationResponse:
    try:
        installation = SkillCatalog().install_or_update(
            db=db,
            user_id=current_user.id,
            skill_key=skill_key,
            is_enabled=payload.is_enabled,
        )
    except SkillCatalogError as exc:
        status_code = status.HTTP_404_NOT_FOUND if "不存在" in str(exc) else status.HTTP_409_CONFLICT
        raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    item = next(
        (
            entry
            for entry in SkillCatalog().list_for_user(db=db, user_id=current_user.id)
            if entry["skill_key"] == installation.skill_key
        ),
        None,
    )
    if not item:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Skill 安装状态读取失败")
    return SkillInstallationResponse(**item)


@router.post("/skills/{skill_key}/upgrade", response_model=SkillInstallationResponse)
def upgrade_skill(
    skill_key: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SkillInstallationResponse:
    catalog = SkillCatalog()
    existing = next(
        (
            item
            for item in catalog.list_for_user(db=db, user_id=current_user.id)
            if item["skill_key"] == skill_key and item["is_installed"]
        ),
        None,
    )
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill 尚未安装。")
    try:
        catalog.install_or_update(
            db=db,
            user_id=current_user.id,
            skill_key=skill_key,
            is_enabled=bool(existing["is_enabled"]),
        )
    except SkillCatalogError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    item = next(
        entry for entry in catalog.list_for_user(db=db, user_id=current_user.id) if entry["skill_key"] == skill_key
    )
    return SkillInstallationResponse(**item)


@router.post("/skills/{skill_key}/rollback", response_model=SkillInstallationResponse)
def rollback_skill(
    skill_key: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> SkillInstallationResponse:
    catalog = SkillCatalog()
    try:
        catalog.rollback(db=db, user_id=current_user.id, skill_key=skill_key)
    except SkillCatalogError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    item = next(
        entry for entry in catalog.list_for_user(db=db, user_id=current_user.id) if entry["skill_key"] == skill_key
    )
    return SkillInstallationResponse(**item)


@router.get("/skill-recommendations", response_model=list[SkillRecommendationResponse])
def recommend_skills(
    query: str,
    project_id: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[SkillRecommendationResponse]:
    if project_id and not ProjectRepository(db).get_by_user(project_id, current_user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return [
        SkillRecommendationResponse(**item)
        for item in SkillRecommendationService().recommend(
            db=db,
            user_id=current_user.id,
            project_id=project_id,
            query=query,
        )
    ]


@router.get("/skill-gold-set")
def get_skill_gold_set_report(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    # The catalog is loaded here as a readiness check for the current account;
    # no Provider call is made by merely viewing the evaluation baseline.
    SkillCatalog().list_for_user(db=db, user_id=current_user.id)
    return SkillGoldSetEvaluator().empty_report()


@router.post("/skill-gold-set/assess")
def assess_skill_gold_set_case(
    payload: SkillGoldSetAssessmentRequest,
    project_id: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    if project_id and not ProjectRepository(db).get_by_user(project_id, current_user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    try:
        return SkillGoldSetEvaluator().assess_case(
            db=db,
            user_id=current_user.id,
            project_id=project_id,
            case_id=payload.case_id,
            selected_skill_key=payload.selected_skill_key,
            plan=payload.plan,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.post("/skill-gold-set/assess-batch")
def assess_skill_gold_set_batch(
    payload: SkillGoldSetBatchAssessmentRequest,
    project_id: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    if project_id and not ProjectRepository(db).get_by_user(project_id, current_user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    try:
        return SkillGoldSetEvaluator().assess_batch(
            db=db,
            user_id=current_user.id,
            project_id=project_id,
            observations=[item.model_dump() for item in payload.observations],
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


@router.patch("/credentials/{provider_key}", response_model=UserToolCredentialResponse)
def update_tool_credential(
    provider_key: str,
    payload: UserToolCredentialUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserToolCredentialResponse:
    repo = ToolConfigRepository(db)
    registry = ToolCatalog(db=db, user_id=current_user.id)
    valid_providers = {tool.credential_provider for tool in registry.list_definitions()}
    valid_providers.update({server.credential_provider or server.server_key for server in repo.list_mcp_servers(current_user.id)})
    if provider_key not in valid_providers:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tool provider not found")

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
    if data.get("clear_api_key"):
        credential.api_key = None
    elif "api_key" in data and data["api_key"] is not None:
        credential.api_key = secret_service.encrypt(data["api_key"])
    if "is_enabled" in data and data["is_enabled"] is not None:
        credential.is_enabled = bool(data["is_enabled"])

    saved = repo.save_credential(credential)
    return UserToolCredentialResponse(
        provider_key=provider_key,
        credential_name=saved.credential_name,
        is_enabled=saved.is_enabled,
        has_api_key=bool(secret_service.decrypt(saved.api_key)),
        api_key_masked=secret_service.mask(secret_service.decrypt(saved.api_key)),
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
    if tool_key not in {tool.tool_key for tool in ToolCatalog(db=db, user_id=current_user.id).list_definitions()}:
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


@router.patch(
    "/workspace-policies/{project_id}",
    response_model=WorkspaceAgentPolicyResponse,
)
def update_workspace_agent_policy(
    project_id: str,
    payload: WorkspaceAgentPolicyUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> WorkspaceAgentPolicyResponse:
    if not ProjectRepository(db).get_by_user(project_id, current_user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    repo = ToolConfigRepository(db)
    policy = repo.get_workspace_policy(project_id)
    if not policy:
        policy = WorkspaceAgentPolicy(
            project_id=project_id,
            permission_mode=payload.permission_mode,
        )
    else:
        policy.permission_mode = payload.permission_mode
    saved = repo.save_workspace_policy(policy)
    return WorkspaceAgentPolicyResponse(
        project_id=saved.project_id,
        permission_mode=saved.permission_mode,
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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="工具连接测试失败，请检查凭证和网络配置。",
        ) from exc

    return ToolConnectionTestResponse(
        ok=True,
        provider_key=provider_key,
        message=f"连接成功，返回 {len(sources)} 个来源，凭证来源：{credential.source}",
    )


@router.post("/mcp-servers", response_model=McpServerResponse)
def create_mcp_server(
    payload: McpServerCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> McpServerResponse:
    repo = ToolConfigRepository(db)
    server_key = _slug(payload.server_key)
    if repo.get_mcp_server_by_key(user_id=current_user.id, server_key=server_key):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="MCP server key already exists")
    try:
        validate_mcp_endpoint_url(payload.url.strip(), auth_type=payload.auth_type)
    except McpEndpointPolicyError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    server = McpServer(
        user_id=current_user.id,
        project_id=(payload.project_id.strip() if payload.project_id else None),
        server_key=server_key,
        name=payload.name.strip(),
        description=(payload.description or "").strip() or None,
        transport_type=payload.transport_type,
        url=payload.url.strip(),
        auth_type=payload.auth_type,
        credential_provider=(payload.credential_provider or server_key).strip(),
        is_enabled=payload.is_enabled,
    )
    if server.project_id and not ProjectRepository(db).get_by_user(server.project_id, current_user.id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return _server_response(repo.save_mcp_server(server))


@router.patch("/mcp-servers/{server_id}", response_model=McpServerResponse)
def update_mcp_server(
    server_id: str,
    payload: McpServerUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> McpServerResponse:
    repo = ToolConfigRepository(db)
    server = repo.get_mcp_server(user_id=current_user.id, server_id=server_id)
    if not server:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found")
    data = payload.model_dump(exclude_unset=True)
    candidate_url = str(data.get("url") or server.url).strip()
    candidate_auth_type = str(data.get("auth_type") or server.auth_type)
    try:
        validate_mcp_endpoint_url(candidate_url, auth_type=candidate_auth_type)
    except McpEndpointPolicyError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    if "name" in data and data["name"] is not None:
        server.name = data["name"].strip() or server.name
    if "description" in data:
        server.description = (data["description"] or "").strip() or None
    if "url" in data and data["url"] is not None:
        server.url = candidate_url
    if "transport_type" in data and data["transport_type"] is not None:
        server.transport_type = data["transport_type"]
    if "auth_type" in data and data["auth_type"] is not None:
        server.auth_type = data["auth_type"]
    if "credential_provider" in data and data["credential_provider"] is not None:
        server.credential_provider = data["credential_provider"].strip() or server.server_key
    if "project_id" in data:
        project_id = (str(data["project_id"]).strip() if data["project_id"] else None)
        if project_id and not ProjectRepository(db).get_by_user(project_id, current_user.id):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
        server.project_id = project_id
    if "is_enabled" in data and data["is_enabled"] is not None:
        server.is_enabled = bool(data["is_enabled"])
    return _server_response(repo.save_mcp_server(server))


@router.delete("/mcp-servers/{server_id}")
def delete_mcp_server(
    server_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, bool]:
    repo = ToolConfigRepository(db)
    server = repo.get_mcp_server(user_id=current_user.id, server_id=server_id)
    if not server:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found")
    repo.delete_mcp_server(server)
    return {"ok": True}


async def _list_mcp_tools_for_server(
    *,
    server: McpServer,
    credential: ToolCredentialResolver,
    user_id: str,
) -> list:
    provider_key = server.credential_provider or server.server_key
    resolved = credential.resolve(user_id=user_id, provider_key=provider_key)
    needs_api_key = server.auth_type != "none" or "{api_key}" in server.url
    if needs_api_key and not resolved.api_key:
        raise RuntimeError(f"MCP server {server.name} 需要配置凭据：{provider_key}")
    endpoint, headers = _mcp_endpoint(server, resolved.api_key)
    await enforce_mcp_endpoint_target_policy(endpoint)
    return await McpHttpClient(endpoint=endpoint, extra_headers=headers).list_tools()


@router.post("/mcp-servers/{server_id}/test", response_model=ToolConnectionTestResponse)
async def test_mcp_server(
    server_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ToolConnectionTestResponse:
    repo = ToolConfigRepository(db)
    server = repo.get_mcp_server(user_id=current_user.id, server_id=server_id)
    if not server:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found")
    try:
        tools = await _list_mcp_tools_for_server(
            server=server,
            credential=ToolCredentialResolver(db),
            user_id=current_user.id,
        )
        server.last_error = None
        repo.save_mcp_server(server)
    except Exception as exc:
        server.last_error = "MCP 连接失败，请检查地址、凭证、网络和 Server 配置。"
        repo.save_mcp_server(server)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=server.last_error) from exc
    return ToolConnectionTestResponse(
        ok=True,
        provider_key=server.credential_provider or server.server_key,
        message=f"MCP 连接成功，发现 {len(tools)} 个工具。",
    )


@router.post("/mcp-servers/{server_id}/sync-tools", response_model=McpSyncResponse)
async def sync_mcp_tools(
    server_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> McpSyncResponse:
    repo = ToolConfigRepository(db)
    server = repo.get_mcp_server(user_id=current_user.id, server_id=server_id)
    if not server:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP server not found")
    try:
        remote_tools = await _list_mcp_tools_for_server(
            server=server,
            credential=ToolCredentialResolver(db),
            user_id=current_user.id,
        )
    except Exception as exc:
        server.last_error = "MCP 同步失败，请检查地址、凭证、网络和 Server 响应。"
        repo.save_mcp_server(server)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=server.last_error) from exc

    existing = {tool.raw_name: tool for tool in repo.list_mcp_tools_for_server(user_id=current_user.id, server_id=server.id)}
    now = datetime.now(timezone.utc)
    saved_tools: list[McpTool] = []
    for remote in remote_tools:
        remote_name = remote.name.strip()
        raw_name = remote_name[:160]
        if not raw_name:
            continue
        annotations = remote.raw.get("annotations") if isinstance(remote.raw, dict) else {}
        annotations = annotations if isinstance(annotations, dict) else {}
        tool = existing.get(raw_name)
        if not tool:
            tool = McpTool(
                server_id=server.id,
                raw_name=raw_name,
                tool_key=_dynamic_tool_key(server_id=server.id, raw_name=remote_name),
                display_name=raw_name[:128],
                is_enabled=False,
            )
        input_schema_json = _json_dumps(remote.input_schema or {})
        output_schema_json = _json_dumps(remote.output_schema or {})
        annotations_json = _json_dumps(annotations)
        tool.description = remote.description or tool.description
        # readOnlyHint 来自远程、不可信 Server，只展示给用户参考，不直接升级为本地低风险策略。
        # 首次发现、尚未审核，或 Schema/annotations 发生变化时，必须重新禁用并人工审核。
        apply_remote_tool_security_policy(
            tool=tool,
            input_schema_json=input_schema_json,
            output_schema_json=output_schema_json,
            annotations_json=annotations_json,
        )
        tool.last_seen_at = now
        repo.flush_mcp_tool(tool)
        saved_tools.append(tool)

    seen_names = {tool.raw_name for tool in saved_tools}
    for stale_name, stale_tool in existing.items():
        if stale_name in seen_names:
            continue
        stale_tool.is_enabled = False
        stale_tool.risk_reviewed = False
        stale_tool.read_only = False
        stale_tool.risk_level = "high"
        repo.flush_mcp_tool(stale_tool)

    server.last_sync_at = now
    server.last_error = None
    db.add(server)
    db.commit()
    for tool in saved_tools:
        db.refresh(tool)
    db.refresh(server)
    return McpSyncResponse(
        ok=True,
        server=_server_response(server),
        tools=[_tool_response(tool, server) for tool in saved_tools],
        message=f"同步完成，发现 {len(saved_tools)} 个工具。新工具默认未启用。",
    )


@router.patch("/mcp-tools/{tool_id}", response_model=McpToolResponse)
def update_mcp_tool(
    tool_id: str,
    payload: McpToolUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> McpToolResponse:
    repo = ToolConfigRepository(db)
    result = repo.get_mcp_tool(user_id=current_user.id, tool_id=tool_id)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP tool not found")
    tool, server = result
    data = payload.model_dump(exclude_unset=True)
    if "display_name" in data and data["display_name"] is not None:
        tool.display_name = data["display_name"].strip() or tool.display_name
    if "description_override" in data:
        tool.description_override = (data["description_override"] or "").strip() or None
    if "category" in data and data["category"] is not None:
        tool.category = _slug(data["category"])[:64] or tool.category
    if "risk_level" in data and data["risk_level"] is not None:
        tool.risk_level = data["risk_level"]
    if "read_only" in data and data["read_only"] is not None:
        tool.read_only = bool(data["read_only"])
    if "risk_reviewed" in data and data["risk_reviewed"] is not None:
        tool.risk_reviewed = bool(data["risk_reviewed"])
        if not tool.risk_reviewed:
            tool.is_enabled = False
            tool.read_only = False
            tool.risk_level = "high"
    if "is_enabled" in data and data["is_enabled"] is not None:
        if data["is_enabled"] and not tool.risk_reviewed:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="MCP tool risk has not been reviewed",
            )
        tool.is_enabled = bool(data["is_enabled"])
    if "fixed_arguments" in data and data["fixed_arguments"] is not None:
        tool.fixed_arguments_json = _json_dumps(data["fixed_arguments"])
    saved = repo.save_mcp_tool(tool)
    return _tool_response(saved, server)


@router.post("/mcp-tools/{tool_id}/test", response_model=ToolConnectionTestResponse)
async def test_mcp_tool(
    tool_id: str,
    payload: McpToolTestRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ToolConnectionTestResponse:
    repo = ToolConfigRepository(db)
    result = repo.get_mcp_tool(user_id=current_user.id, tool_id=tool_id)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="MCP tool not found")
    tool, server = result
    if (
        not server.is_enabled
        or not tool.is_enabled
        or not tool.risk_reviewed
        or not tool.read_only
        or tool.risk_level == "high"
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="MCP 工具尚未通过低风险只读审核，禁止执行测试。",
        )
    provider_key = server.credential_provider or server.server_key
    credential = ToolCredentialResolver(db).resolve(user_id=current_user.id, provider_key=provider_key)
    needs_api_key = server.auth_type != "none" or "{api_key}" in server.url
    if needs_api_key and not credential.api_key:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"MCP 工具需要配置凭据：{provider_key}")
    endpoint, headers = _mcp_endpoint(server, credential.api_key)
    try:
        await enforce_mcp_endpoint_target_policy(endpoint)
        fixed_arguments = _json_loads(tool.fixed_arguments_json, {})
        effective_arguments = {
            **(payload.arguments or {}),
            **(fixed_arguments if isinstance(fixed_arguments, dict) else {}),
        }
        response = await McpHttpClient(endpoint=endpoint, extra_headers=headers).call_tool(
            tool_name=tool.raw_name,
            arguments=effective_arguments,
            output_schema=_json_loads(tool.output_schema_json, {}) or None,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="MCP 工具测试失败，请检查参数、网络和输出 Schema。",
        ) from exc
    return ToolConnectionTestResponse(
        ok=True,
        provider_key=provider_key,
        message=f"MCP 工具 {tool.display_name} 调用成功。",
        raw=response.raw,
    )
