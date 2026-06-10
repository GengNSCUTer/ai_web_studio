import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.repositories.setting_repo import UserSettingRepository
from app.schemas.setting import (
    KnowledgeModelOptionsRequest,
    KnowledgeModelOptionsResponse,
    ProviderConnectionTestRequest,
    ProviderConnectionTestResponse,
    UserSettingResponse,
    UserSettingUpdate,
)
from app.services.chat_provider_service import ChatProviderService, resolve_provider_base_url
from app.services.knowledge_model_catalog_service import KnowledgeModelCatalogService
from app.services.setting_service import SettingService

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("", response_model=UserSettingResponse)
def get_settings(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserSettingResponse:
    service = SettingService(UserSettingRepository(db))
    return service.get_or_create_user_settings(current_user.id)


@router.patch("", response_model=UserSettingResponse)
def update_settings(
    payload: UserSettingUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> UserSettingResponse:
    service = SettingService(UserSettingRepository(db))
    return service.update_user_settings(current_user.id, payload)


@router.post("/test-provider", response_model=ProviderConnectionTestResponse)
async def test_provider_connection(
    payload: ProviderConnectionTestRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ProviderConnectionTestResponse:
    service = SettingService(UserSettingRepository(db))
    current_settings = service.get_or_create_user_settings(current_user.id)

    provider_type = payload.provider_type
    base_url = resolve_provider_base_url(
        provider_type=provider_type,
        configured_base_url=payload.ollama_base_url,
    )
    api_key = None
    if provider_type == "openai-compatible":
        api_key = payload.api_key if payload.api_key is not None else service.resolve_provider_api_key(current_user.id)

    try:
        models = await ChatProviderService().list_models(
            provider_type=provider_type,
            base_url=base_url,
            api_key=api_key,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=400, detail=f"连接失败：{exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"连接失败：{exc}") from exc

    resolved_default_model = (
        current_settings.default_model
        if current_settings.provider_type == provider_type and current_settings.default_model in models
        else (models[0] if models else None)
    )

    return ProviderConnectionTestResponse(
        ok=True,
        provider=provider_type,
        base_url=base_url,
        models=models,
        default_model=resolved_default_model,
        message=f"连接成功，获取到 {len(models)} 个模型",
    )


@router.post("/knowledge-model-options", response_model=KnowledgeModelOptionsResponse)
async def get_knowledge_model_options(
    payload: KnowledgeModelOptionsRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> KnowledgeModelOptionsResponse:
    service = SettingService(UserSettingRepository(db))
    provider = service.normalize_knowledge_model_provider(payload.provider)
    base_url = payload.base_url.strip()
    normalized_kind = "rerank" if payload.model_kind == "rerank" else "embedding"
    api_key = (
        payload.api_key
        if payload.api_key is not None
        else service.resolve_knowledge_model_api_key(current_user.id, normalized_kind)
    )
    try:
        models, source = await KnowledgeModelCatalogService().list_options(
            provider=provider,
            base_url=base_url,
            api_key=api_key,
            model_kind=normalized_kind,
            strict=payload.strict,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=400, detail=f"连接失败：{exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"连接失败：{exc}") from exc
    return KnowledgeModelOptionsResponse(
        ok=True,
        provider=provider,
        base_url=base_url,
        model_kind=normalized_kind,
        models=models,
        source=source,
        message=f"获取到 {len(models)} 个候选模型，来源：{source}",
    )
