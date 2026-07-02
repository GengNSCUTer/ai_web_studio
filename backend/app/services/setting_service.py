from app.core.config import settings as app_settings
from app.models.user_setting import UserSetting
from app.repositories.setting_repo import UserSettingRepository
from app.schemas.setting import UserSettingResponse, UserSettingUpdate
from app.services.context_governance_service import ContextBudgetPlanner
from app.services.knowledge_model_metadata import infer_embedding_dimensions
from app.services.secret_service import SecretService


class SettingService:
    DEFAULT_OPENAI_BASE_URL = "https://api.siliconflow.cn/v1"
    DEFAULT_OPENAI_MODEL = "Qwen/Qwen3.5-35B-A3B"
    DEFAULT_OPENAI_API_KEY = None
    DEFAULT_CONTEXT_MODE = "balanced"
    DEFAULT_OPENAI_CONTEXT_WINDOW = 128000
    DEFAULT_OLLAMA_CONTEXT_WINDOW = 100000
    DEFAULT_UI_LANGUAGE = "zh-CN"
    DEFAULT_THEME_MODE = "system"
    DEFAULT_MEMORY_ENABLED = True
    DEFAULT_MEMORY_MAX_CHARS = 4000
    DEFAULT_KNOWLEDGE_PARSER_PROVIDER = "local_basic"
    DEFAULT_KNOWLEDGE_MODEL_BASE_URL = "https://api.siliconflow.cn/v1"
    DEFAULT_KNOWLEDGE_EMBEDDING_PROVIDER = "siliconflow"
    DEFAULT_KNOWLEDGE_EMBEDDING_MODEL = "BAAI/bge-m3"
    DEFAULT_KNOWLEDGE_EMBEDDING_DIMENSIONS = 1024
    DEFAULT_KNOWLEDGE_RERANK_ENABLED = True
    DEFAULT_KNOWLEDGE_RERANK_PROVIDER = "siliconflow"
    DEFAULT_KNOWLEDGE_RERANK_MODEL = "BAAI/bge-reranker-v2-m3"

    def __init__(self, repo: UserSettingRepository):
        self.repo = repo
        self.secrets = SecretService()

    @staticmethod
    def _normalize_optional_str(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @classmethod
    def _looks_like_api_base_url(cls, value: str | None) -> bool:
        normalized = (value or "").strip().lower()
        if not normalized:
            return False
        return normalized.endswith("/v1") or "api." in normalized or "siliconflow" in normalized

    @staticmethod
    def normalize_context_mode(value: str | None) -> str:
        normalized = (value or "").strip() or "balanced"
        if normalized not in ContextBudgetPlanner.MODE_CHAR_RATIOS:
            return "balanced"
        return normalized

    @staticmethod
    def normalize_theme_mode(value: str | None) -> str:
        normalized = (value or "").strip() or "system"
        if normalized not in {"system", "light", "dark"}:
            return "system"
        return normalized

    @classmethod
    def normalize_knowledge_parser_provider(cls, value: str | None) -> str:
        normalized = (value or "").strip() or cls.DEFAULT_KNOWLEDGE_PARSER_PROVIDER
        if normalized not in {"local_basic", "mineru"}:
            return cls.DEFAULT_KNOWLEDGE_PARSER_PROVIDER
        return normalized

    @staticmethod
    def normalize_knowledge_model_provider(value: str | None) -> str:
        normalized = (value or "").strip() or "siliconflow"
        if normalized not in {"siliconflow", "ollama", "openai-compatible"}:
            return "siliconflow"
        return normalized

    @classmethod
    def normalize_knowledge_embedding_dimensions(cls, value: int | None) -> int:
        if value is None:
            return cls.DEFAULT_KNOWLEDGE_EMBEDDING_DIMENSIONS
        return max(128, min(int(value), 4096))

    @classmethod
    def normalize_context_window(cls, value: int | None, provider_type: str) -> int:
        default_value = cls.default_context_window_for_provider(provider_type)
        if value is None:
            candidate = default_value
        else:
            candidate = int(value)
        return max(
            ContextBudgetPlanner.MIN_CONTEXT_WINDOW,
            min(candidate, ContextBudgetPlanner.HARD_MAX_CONTEXT_WINDOW),
        )

    @classmethod
    def default_context_window_for_provider(cls, provider_type: str) -> int:
        if provider_type == "ollama":
            return cls.DEFAULT_OLLAMA_CONTEXT_WINDOW
        return cls.DEFAULT_OPENAI_CONTEXT_WINDOW

    @classmethod
    def _build_default_setting(cls, user_id: str) -> UserSetting:
        return UserSetting(
            user_id=user_id,
            provider_type="openai-compatible",
            default_model=cls.DEFAULT_OPENAI_MODEL,
            ollama_base_url=app_settings.ollama_base_url,
            api_base_url=cls.DEFAULT_OPENAI_BASE_URL,
            api_key=cls.DEFAULT_OPENAI_API_KEY,
            temperature=0.7,
            top_p=0.9,
            max_tokens=None,
            system_prompt=None,
            model_context_window=cls.DEFAULT_OPENAI_CONTEXT_WINDOW,
            context_mode=cls.DEFAULT_CONTEXT_MODE,
            memory_enabled=cls.DEFAULT_MEMORY_ENABLED,
            memory_max_chars=cls.DEFAULT_MEMORY_MAX_CHARS,
            ui_language=cls.DEFAULT_UI_LANGUAGE,
            theme_mode=cls.DEFAULT_THEME_MODE,
            knowledge_parser_provider=cls.DEFAULT_KNOWLEDGE_PARSER_PROVIDER,
            knowledge_embedding_provider=cls.DEFAULT_KNOWLEDGE_EMBEDDING_PROVIDER,
            knowledge_embedding_base_url=cls.DEFAULT_KNOWLEDGE_MODEL_BASE_URL,
            knowledge_embedding_model=cls.DEFAULT_KNOWLEDGE_EMBEDDING_MODEL,
            knowledge_embedding_dimensions=cls.DEFAULT_KNOWLEDGE_EMBEDDING_DIMENSIONS,
            knowledge_rerank_enabled=cls.DEFAULT_KNOWLEDGE_RERANK_ENABLED,
            knowledge_rerank_provider=cls.DEFAULT_KNOWLEDGE_RERANK_PROVIDER,
            knowledge_rerank_base_url=cls.DEFAULT_KNOWLEDGE_MODEL_BASE_URL,
            knowledge_rerank_model=cls.DEFAULT_KNOWLEDGE_RERANK_MODEL,
            knowledge_embedding_api_key=None,
            knowledge_rerank_api_key=None,
            knowledge_api_key=None,
        )

    def get_or_create_user_settings(self, user_id: str) -> UserSettingResponse:
        setting = self.repo.get_by_user(user_id)
        if not setting:
            setting = self._build_default_setting(user_id)
            setting = self.repo.save(setting)
        else:
            should_save = False
            if not getattr(setting, "provider_type", None):
                setting.provider_type = "openai-compatible"
                should_save = True
            if not getattr(setting, "api_base_url", None):
                if setting.provider_type == "openai-compatible" and self._looks_like_api_base_url(
                    getattr(setting, "ollama_base_url", None)
                ):
                    setting.api_base_url = setting.ollama_base_url
                else:
                    setting.api_base_url = self.DEFAULT_OPENAI_BASE_URL
                should_save = True
            if setting.provider_type == "openai-compatible":
                if setting.default_model == app_settings.ollama_default_model:
                    setting.default_model = self.DEFAULT_OPENAI_MODEL
                    should_save = True
                if self._looks_like_api_base_url(getattr(setting, "ollama_base_url", None)):
                    setting.api_base_url = setting.ollama_base_url
                    setting.ollama_base_url = app_settings.ollama_base_url
                    should_save = True
            if not getattr(setting, "ollama_base_url", None):
                setting.ollama_base_url = app_settings.ollama_base_url
                should_save = True
            if not getattr(setting, "model_context_window", None):
                setting.model_context_window = self.normalize_context_window(
                    getattr(setting, "model_context_window", None),
                    setting.provider_type,
                )
                should_save = True
            if not getattr(setting, "context_mode", None):
                setting.context_mode = self.DEFAULT_CONTEXT_MODE
                should_save = True
            if not getattr(setting, "ui_language", None):
                setting.ui_language = self.DEFAULT_UI_LANGUAGE
                should_save = True
            if not getattr(setting, "theme_mode", None):
                setting.theme_mode = self.DEFAULT_THEME_MODE
                should_save = True
            if getattr(setting, "memory_enabled", None) is None:
                setting.memory_enabled = self.DEFAULT_MEMORY_ENABLED
                should_save = True
            if not getattr(setting, "memory_max_chars", None):
                setting.memory_max_chars = self.DEFAULT_MEMORY_MAX_CHARS
                should_save = True
            if not getattr(setting, "knowledge_parser_provider", None):
                setting.knowledge_parser_provider = self.DEFAULT_KNOWLEDGE_PARSER_PROVIDER
                should_save = True
            if not getattr(setting, "knowledge_embedding_provider", None):
                setting.knowledge_embedding_provider = self.DEFAULT_KNOWLEDGE_EMBEDDING_PROVIDER
                should_save = True
            if not getattr(setting, "knowledge_embedding_base_url", None):
                setting.knowledge_embedding_base_url = self.DEFAULT_KNOWLEDGE_MODEL_BASE_URL
                should_save = True
            if not getattr(setting, "knowledge_embedding_model", None):
                setting.knowledge_embedding_model = self.DEFAULT_KNOWLEDGE_EMBEDDING_MODEL
                should_save = True
            if not getattr(setting, "knowledge_embedding_dimensions", None):
                setting.knowledge_embedding_dimensions = self.DEFAULT_KNOWLEDGE_EMBEDDING_DIMENSIONS
                should_save = True
            requested_dimensions = self.normalize_knowledge_embedding_dimensions(
                getattr(setting, "knowledge_embedding_dimensions", None)
            )
            inferred_dimensions = infer_embedding_dimensions(
                getattr(setting, "knowledge_embedding_model", None),
                requested_dimensions,
            )
            if inferred_dimensions != setting.knowledge_embedding_dimensions:
                setting.knowledge_embedding_dimensions = inferred_dimensions
                should_save = True
            if getattr(setting, "knowledge_rerank_enabled", None) is None:
                setting.knowledge_rerank_enabled = self.DEFAULT_KNOWLEDGE_RERANK_ENABLED
                should_save = True
            if not getattr(setting, "knowledge_rerank_provider", None):
                setting.knowledge_rerank_provider = self.DEFAULT_KNOWLEDGE_RERANK_PROVIDER
                should_save = True
            if not getattr(setting, "knowledge_rerank_base_url", None):
                setting.knowledge_rerank_base_url = self.DEFAULT_KNOWLEDGE_MODEL_BASE_URL
                should_save = True
            if not getattr(setting, "knowledge_rerank_model", None):
                setting.knowledge_rerank_model = self.DEFAULT_KNOWLEDGE_RERANK_MODEL
                should_save = True
            if getattr(setting, "context_mode", None):
                normalized_mode = self.normalize_context_mode(setting.context_mode)
                if normalized_mode != setting.context_mode:
                    setting.context_mode = normalized_mode
                    should_save = True
            if getattr(setting, "model_context_window", None):
                normalized_window = self.normalize_context_window(
                    setting.model_context_window,
                    setting.provider_type,
                )
                if normalized_window != setting.model_context_window:
                    setting.model_context_window = normalized_window
                    should_save = True
            normalized_theme = self.normalize_theme_mode(getattr(setting, "theme_mode", None))
            if normalized_theme != getattr(setting, "theme_mode", None):
                setting.theme_mode = normalized_theme
                should_save = True
            if should_save:
                setting = self.repo.save(setting)
        return self._to_response(setting)

    def update_user_settings(self, user_id: str, payload: UserSettingUpdate) -> UserSettingResponse:
        setting = self.repo.get_by_user(user_id)
        if not setting:
            setting = self._build_default_setting(user_id)

        data = payload.model_dump(exclude_unset=True)
        for key in ("provider_type", "default_model", "ollama_base_url", "api_base_url", "system_prompt"):
            if key in data:
                data[key] = self._normalize_optional_str(data[key])
        for key in (
            "knowledge_parser_provider",
            "knowledge_embedding_provider",
            "knowledge_embedding_base_url",
            "knowledge_embedding_model",
            "knowledge_rerank_provider",
            "knowledge_rerank_base_url",
            "knowledge_rerank_model",
        ):
            if key in data:
                data[key] = self._normalize_optional_str(data[key])
        for key, value in data.items():
            if key in {
                "api_key",
                "clear_api_key",
                "knowledge_api_key",
                "clear_knowledge_api_key",
                "knowledge_embedding_api_key",
                "clear_knowledge_embedding_api_key",
                "knowledge_rerank_api_key",
                "clear_knowledge_rerank_api_key",
            }:
                continue
            setattr(setting, key, value)

        if data.get("clear_api_key"):
            setting.api_key = None
        elif "api_key" in data and data["api_key"] is not None:
            setting.api_key = self.secrets.encrypt(data["api_key"])

        if data.get("clear_knowledge_api_key"):
            setting.knowledge_api_key = None
        elif "knowledge_api_key" in data and data["knowledge_api_key"] is not None:
            setting.knowledge_api_key = self.secrets.encrypt(data["knowledge_api_key"])

        if data.get("clear_knowledge_embedding_api_key"):
            setting.knowledge_embedding_api_key = None
        elif "knowledge_embedding_api_key" in data and data["knowledge_embedding_api_key"] is not None:
            setting.knowledge_embedding_api_key = self.secrets.encrypt(data["knowledge_embedding_api_key"])

        if data.get("clear_knowledge_rerank_api_key"):
            setting.knowledge_rerank_api_key = None
        elif "knowledge_rerank_api_key" in data and data["knowledge_rerank_api_key"] is not None:
            setting.knowledge_rerank_api_key = self.secrets.encrypt(data["knowledge_rerank_api_key"])

        if "provider_type" in data and "model_context_window" not in data:
            setting.model_context_window = self.default_context_window_for_provider(setting.provider_type)
        if not getattr(setting, "ollama_base_url", None):
            setting.ollama_base_url = app_settings.ollama_base_url
        if not getattr(setting, "api_base_url", None):
            setting.api_base_url = self.DEFAULT_OPENAI_BASE_URL
        if setting.provider_type == "openai-compatible" and self._looks_like_api_base_url(
            getattr(setting, "ollama_base_url", None)
        ):
            if "api_base_url" not in data or not data.get("api_base_url"):
                setting.api_base_url = setting.ollama_base_url
            setting.ollama_base_url = app_settings.ollama_base_url
        setting.context_mode = self.normalize_context_mode(getattr(setting, "context_mode", None))
        setting.model_context_window = self.normalize_context_window(
            getattr(setting, "model_context_window", None),
            setting.provider_type,
        )
        if not getattr(setting, "ui_language", None):
            setting.ui_language = self.DEFAULT_UI_LANGUAGE
        setting.theme_mode = self.normalize_theme_mode(getattr(setting, "theme_mode", None))
        if getattr(setting, "memory_enabled", None) is None:
            setting.memory_enabled = self.DEFAULT_MEMORY_ENABLED
        if not getattr(setting, "memory_max_chars", None):
            setting.memory_max_chars = self.DEFAULT_MEMORY_MAX_CHARS
        setting.memory_max_chars = max(500, min(int(setting.memory_max_chars), 20000))
        setting.knowledge_parser_provider = self.normalize_knowledge_parser_provider(
            getattr(setting, "knowledge_parser_provider", None)
        )
        setting.knowledge_embedding_provider = self.normalize_knowledge_model_provider(
            getattr(setting, "knowledge_embedding_provider", None)
        )
        if not getattr(setting, "knowledge_embedding_base_url", None):
            setting.knowledge_embedding_base_url = self.DEFAULT_KNOWLEDGE_MODEL_BASE_URL
        if not getattr(setting, "knowledge_embedding_model", None):
            setting.knowledge_embedding_model = self.DEFAULT_KNOWLEDGE_EMBEDDING_MODEL
        requested_dimensions = self.normalize_knowledge_embedding_dimensions(
            getattr(setting, "knowledge_embedding_dimensions", None)
        )
        setting.knowledge_embedding_dimensions = infer_embedding_dimensions(
            setting.knowledge_embedding_model,
            requested_dimensions,
        )
        if getattr(setting, "knowledge_rerank_enabled", None) is None:
            setting.knowledge_rerank_enabled = self.DEFAULT_KNOWLEDGE_RERANK_ENABLED
        setting.knowledge_rerank_provider = self.normalize_knowledge_model_provider(
            getattr(setting, "knowledge_rerank_provider", None)
        )
        if not getattr(setting, "knowledge_rerank_base_url", None):
            setting.knowledge_rerank_base_url = self.DEFAULT_KNOWLEDGE_MODEL_BASE_URL
        if not getattr(setting, "knowledge_rerank_model", None):
            setting.knowledge_rerank_model = self.DEFAULT_KNOWLEDGE_RERANK_MODEL

        saved = self.repo.save(setting)
        return self._to_response(saved)

    def resolve_provider_api_key(self, user_id: str) -> str | None:
        setting = self.repo.get_by_user(user_id)
        if not setting:
            return None
        return self.secrets.decrypt(getattr(setting, "api_key", None))

    def resolve_knowledge_api_key(self, user_id: str) -> str | None:
        setting = self.repo.get_by_user(user_id)
        if not setting:
            return None
        return self.secrets.decrypt(getattr(setting, "knowledge_api_key", None))

    def resolve_knowledge_model_api_key(self, user_id: str, model_kind: str) -> str | None:
        setting = self.repo.get_by_user(user_id)
        if not setting:
            return None
        if model_kind == "rerank":
            dedicated = self.secrets.decrypt(getattr(setting, "knowledge_rerank_api_key", None))
        else:
            dedicated = self.secrets.decrypt(getattr(setting, "knowledge_embedding_api_key", None))
        return dedicated or self.secrets.decrypt(getattr(setting, "knowledge_api_key", None))

    def _to_response(self, setting: UserSetting) -> UserSettingResponse:
        raw_api_key = self.secrets.decrypt(getattr(setting, "api_key", None))
        raw_knowledge_embedding_api_key = self.secrets.decrypt(
            getattr(setting, "knowledge_embedding_api_key", None)
        )
        raw_knowledge_rerank_api_key = self.secrets.decrypt(
            getattr(setting, "knowledge_rerank_api_key", None)
        )
        raw_knowledge_api_key = self.secrets.decrypt(getattr(setting, "knowledge_api_key", None))
        return UserSettingResponse.model_validate(
            {
                "id": setting.id,
                "user_id": setting.user_id,
                "provider_type": setting.provider_type,
                "default_model": setting.default_model,
                "ollama_base_url": setting.ollama_base_url,
                "api_base_url": getattr(setting, "api_base_url", self.DEFAULT_OPENAI_BASE_URL),
                "api_key": None,
                "has_api_key": bool(raw_api_key),
                "api_key_masked": self.secrets.mask(raw_api_key),
                "temperature": setting.temperature,
                "top_p": setting.top_p,
                "max_tokens": setting.max_tokens,
                "system_prompt": setting.system_prompt,
                "model_context_window": setting.model_context_window,
                "context_mode": setting.context_mode,
                "memory_enabled": setting.memory_enabled,
                "memory_max_chars": setting.memory_max_chars,
                "ui_language": setting.ui_language,
                "theme_mode": setting.theme_mode,
                "knowledge_parser_provider": getattr(
                    setting, "knowledge_parser_provider", self.DEFAULT_KNOWLEDGE_PARSER_PROVIDER
                ),
                "knowledge_embedding_provider": getattr(
                    setting, "knowledge_embedding_provider", self.DEFAULT_KNOWLEDGE_EMBEDDING_PROVIDER
                ),
                "knowledge_embedding_base_url": getattr(
                    setting, "knowledge_embedding_base_url", self.DEFAULT_KNOWLEDGE_MODEL_BASE_URL
                ),
                "knowledge_embedding_model": getattr(
                    setting, "knowledge_embedding_model", self.DEFAULT_KNOWLEDGE_EMBEDDING_MODEL
                ),
                "knowledge_embedding_dimensions": getattr(
                    setting, "knowledge_embedding_dimensions", self.DEFAULT_KNOWLEDGE_EMBEDDING_DIMENSIONS
                ),
                "knowledge_rerank_enabled": getattr(
                    setting, "knowledge_rerank_enabled", self.DEFAULT_KNOWLEDGE_RERANK_ENABLED
                ),
                "knowledge_rerank_provider": getattr(
                    setting, "knowledge_rerank_provider", self.DEFAULT_KNOWLEDGE_RERANK_PROVIDER
                ),
                "knowledge_rerank_base_url": getattr(
                    setting, "knowledge_rerank_base_url", self.DEFAULT_KNOWLEDGE_MODEL_BASE_URL
                ),
                "knowledge_rerank_model": getattr(
                    setting, "knowledge_rerank_model", self.DEFAULT_KNOWLEDGE_RERANK_MODEL
                ),
                "knowledge_embedding_api_key": None,
                "knowledge_embedding_has_api_key": bool(raw_knowledge_embedding_api_key or raw_knowledge_api_key),
                "knowledge_embedding_api_key_masked": self.secrets.mask(
                    raw_knowledge_embedding_api_key or raw_knowledge_api_key
                ),
                "knowledge_rerank_api_key": None,
                "knowledge_rerank_has_api_key": bool(raw_knowledge_rerank_api_key or raw_knowledge_api_key),
                "knowledge_rerank_api_key_masked": self.secrets.mask(
                    raw_knowledge_rerank_api_key or raw_knowledge_api_key
                ),
                "knowledge_api_key": None,
                "knowledge_has_api_key": bool(raw_knowledge_api_key),
                "knowledge_api_key_masked": self.secrets.mask(raw_knowledge_api_key),
                "updated_at": setting.updated_at,
            }
        )
