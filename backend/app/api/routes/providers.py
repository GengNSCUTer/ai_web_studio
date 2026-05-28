import httpx
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.config import settings
from app.models.user import User
from app.repositories.setting_repo import UserSettingRepository
from app.services.chat_provider_service import ChatProviderService, resolve_provider_base_url
from app.services.setting_service import SettingService

router = APIRouter(prefix="/models", tags=["providers"])


@router.get("")
async def get_provider_defaults(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, object]:
    service = SettingService(UserSettingRepository(db))
    user_settings = service.get_or_create_user_settings(current_user.id)
    provider_type = user_settings.provider_type or "ollama"
    base_url = resolve_provider_base_url(
        provider_type=provider_type,
        configured_base_url=user_settings.ollama_base_url,
    )
    models: list[str] = []
    try:
        models = await ChatProviderService().list_models(
            provider_type=provider_type,
            base_url=base_url,
            api_key=service.resolve_provider_api_key(current_user.id),
        )
    except httpx.HTTPError:
        models = []
    except Exception:
        models = []

    return {
        "provider": provider_type,
        "base_url": base_url or settings.ollama_base_url,
        "default_model": user_settings.default_model or settings.ollama_default_model,
        "models": models,
    }
