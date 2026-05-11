from app.core.config import settings as app_settings
from app.models.user_setting import UserSetting
from app.repositories.setting_repo import UserSettingRepository
from app.schemas.setting import UserSettingResponse, UserSettingUpdate
from app.services.context_governance_service import ContextBudgetPlanner


class SettingService:
    DEFAULT_OPENAI_BASE_URL = "https://api.siliconflow.cn/v1"
    DEFAULT_OPENAI_MODEL = "Qwen/Qwen3.5-35B-A3B"
    DEFAULT_OPENAI_API_KEY = None
    DEFAULT_CONTEXT_MODE = "balanced"
    DEFAULT_OPENAI_CONTEXT_WINDOW = 128000
    DEFAULT_OLLAMA_CONTEXT_WINDOW = 100000
    DEFAULT_UI_LANGUAGE = "zh-CN"
    DEFAULT_MEMORY_ENABLED = True
    DEFAULT_MEMORY_MAX_CHARS = 4000

    def __init__(self, repo: UserSettingRepository):
        self.repo = repo

    @staticmethod
    def _normalize_optional_str(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def normalize_context_mode(value: str | None) -> str:
        normalized = (value or "").strip() or "balanced"
        if normalized not in ContextBudgetPlanner.MODE_CHAR_RATIOS:
            return "balanced"
        return normalized

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

    def get_or_create_user_settings(self, user_id: str) -> UserSettingResponse:
        setting = self.repo.get_by_user(user_id)
        if not setting:
            setting = UserSetting(
                user_id=user_id,
                provider_type="openai-compatible",
                default_model=self.DEFAULT_OPENAI_MODEL,
                ollama_base_url=self.DEFAULT_OPENAI_BASE_URL,
                api_key=self.DEFAULT_OPENAI_API_KEY,
                temperature=0.7,
                top_p=0.9,
                max_tokens=None,
                system_prompt=None,
                model_context_window=self.DEFAULT_OPENAI_CONTEXT_WINDOW,
                context_mode=self.DEFAULT_CONTEXT_MODE,
                memory_enabled=self.DEFAULT_MEMORY_ENABLED,
                memory_max_chars=self.DEFAULT_MEMORY_MAX_CHARS,
                ui_language=self.DEFAULT_UI_LANGUAGE,
            )
            setting = self.repo.save(setting)
        else:
            should_save = False
            if not getattr(setting, "provider_type", None):
                setting.provider_type = "openai-compatible"
                should_save = True
            if setting.provider_type == "openai-compatible":
                if setting.default_model == app_settings.ollama_default_model:
                    setting.default_model = self.DEFAULT_OPENAI_MODEL
                    should_save = True
                if setting.ollama_base_url == app_settings.ollama_base_url:
                    setting.ollama_base_url = self.DEFAULT_OPENAI_BASE_URL
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
            if getattr(setting, "memory_enabled", None) is None:
                setting.memory_enabled = self.DEFAULT_MEMORY_ENABLED
                should_save = True
            if not getattr(setting, "memory_max_chars", None):
                setting.memory_max_chars = self.DEFAULT_MEMORY_MAX_CHARS
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
            if should_save:
                setting = self.repo.save(setting)
        return UserSettingResponse.model_validate(setting)

    def update_user_settings(self, user_id: str, payload: UserSettingUpdate) -> UserSettingResponse:
        setting = self.repo.get_by_user(user_id)
        if not setting:
            setting = UserSetting(
                user_id=user_id,
                provider_type="openai-compatible",
                default_model=self.DEFAULT_OPENAI_MODEL,
                ollama_base_url=self.DEFAULT_OPENAI_BASE_URL,
                api_key=self.DEFAULT_OPENAI_API_KEY,
                temperature=0.7,
                top_p=0.9,
                max_tokens=None,
                system_prompt=None,
                model_context_window=self.DEFAULT_OPENAI_CONTEXT_WINDOW,
                context_mode=self.DEFAULT_CONTEXT_MODE,
                memory_enabled=self.DEFAULT_MEMORY_ENABLED,
                memory_max_chars=self.DEFAULT_MEMORY_MAX_CHARS,
                ui_language=self.DEFAULT_UI_LANGUAGE,
            )

        data = payload.model_dump(exclude_unset=True)
        for key in ("provider_type", "default_model", "ollama_base_url", "api_key", "system_prompt"):
            if key in data:
                data[key] = self._normalize_optional_str(data[key])
        for key, value in data.items():
            setattr(setting, key, value)

        if "provider_type" in data and "model_context_window" not in data:
            setting.model_context_window = self.default_context_window_for_provider(setting.provider_type)
        setting.context_mode = self.normalize_context_mode(getattr(setting, "context_mode", None))
        setting.model_context_window = self.normalize_context_window(
            getattr(setting, "model_context_window", None),
            setting.provider_type,
        )
        if not getattr(setting, "ui_language", None):
            setting.ui_language = self.DEFAULT_UI_LANGUAGE
        if getattr(setting, "memory_enabled", None) is None:
            setting.memory_enabled = self.DEFAULT_MEMORY_ENABLED
        if not getattr(setting, "memory_max_chars", None):
            setting.memory_max_chars = self.DEFAULT_MEMORY_MAX_CHARS
        setting.memory_max_chars = max(500, min(int(setting.memory_max_chars), 20000))

        saved = self.repo.save(setting)
        return UserSettingResponse.model_validate(saved)
