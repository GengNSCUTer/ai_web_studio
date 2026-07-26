from app.core.config import settings as app_settings
from app.models.user_setting import UserSetting
from app.repositories.setting_repo import UserSettingRepository
from app.schemas.setting import UserSettingResponse, UserSettingUpdate
from app.services.context_governance_service import ContextBudgetPlanner
from app.services.knowledge_model_metadata import infer_embedding_dimensions
from app.services.secret_service import SecretService
from sqlalchemy.exc import IntegrityError


class SettingService:
    """用户设置业务层：负责默认值、归一化、密钥加密、响应脱敏。"""

    DEFAULT_OPENAI_BASE_URL = "https://api.siliconflow.cn/v1"
    DEFAULT_OPENAI_MODEL = "Qwen/Qwen3.5-35B-A3B"
    DEFAULT_OPENAI_API_KEY = None
    DEFAULT_CONTEXT_MODE = "balanced"
    DEFAULT_OPENAI_CONTEXT_WINDOW = 128000
    DEFAULT_OLLAMA_CONTEXT_WINDOW = 100000
    DEFAULT_VLLM_BASE_URL = "http://127.0.0.1:8000/v1"
    DEFAULT_VLLM_MODEL = "Qwen/Qwen3-8B"
    DEFAULT_VLLM_CONTEXT_WINDOW = 32768
    ALLOWED_CHAT_PROVIDERS = {"ollama", "openai-compatible", "vllm"}
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

    @staticmethod
    def normalize_max_tokens(value: int | None) -> int | None:
        if value is None:
            return None
        return max(1, min(int(value), ContextBudgetPlanner.HARD_MAX_CONTEXT_WINDOW // 2))

    @classmethod
    def default_context_window_for_provider(cls, provider_type: str) -> int:
        if provider_type == "ollama":
            return cls.DEFAULT_OLLAMA_CONTEXT_WINDOW
        if provider_type == "vllm":
            return cls.DEFAULT_VLLM_CONTEXT_WINDOW
        return cls.DEFAULT_OPENAI_CONTEXT_WINDOW

    @classmethod
    def default_api_base_url_for_provider(cls, provider_type: str) -> str:
        if provider_type == "vllm":
            return cls.DEFAULT_VLLM_BASE_URL
        return cls.DEFAULT_OPENAI_BASE_URL

    @classmethod
    def default_model_for_provider(cls, provider_type: str) -> str:
        if provider_type == "ollama":
            return app_settings.ollama_default_model
        if provider_type == "vllm":
            return cls.DEFAULT_VLLM_MODEL
        return cls.DEFAULT_OPENAI_MODEL

    @classmethod
    def _build_default_setting(cls, user_id: str) -> UserSetting:
        # 新用户首次进入系统时生成一份完整默认配置，后续聊天/RAG都依赖这份设置。
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
        )

    def _save_setting(self, setting: UserSetting) -> UserSetting:
        try:
            saved = self.repo.save(setting)
            self.repo.db.commit()
            self.repo.db.refresh(saved)
            return saved
        except IntegrityError:
            self.repo.db.rollback()
            raise

    def get_or_create_user_settings(self, user_id: str) -> UserSettingResponse:
        setting = self.repo.get_by_user(user_id)
        if not setting:
            setting = self._build_default_setting(user_id)
            try:
                setting = self._save_setting(setting)
            except IntegrityError:
                # 并发首次访问时，另一个请求可能已插入 user_id 唯一记录；回滚后重新读取即可。
                setting = self.repo.get_by_user(user_id)
                if not setting:
                    raise

        if setting:
            # 这里兼容历史版本字段缺失或旧默认值，读取时顺手修正并持久化。
            should_save = False
            if getattr(setting, "provider_type", None) not in self.ALLOWED_CHAT_PROVIDERS:
                setting.provider_type = "openai-compatible"
                should_save = True
            if not getattr(setting, "api_base_url", None):
                if setting.provider_type == "openai-compatible" and self._looks_like_api_base_url(
                    getattr(setting, "ollama_base_url", None)
                ):
                    setting.api_base_url = setting.ollama_base_url
                else:
                    setting.api_base_url = self.default_api_base_url_for_provider(setting.provider_type)
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
            normalized_max_tokens = self.normalize_max_tokens(getattr(setting, "max_tokens", None))
            if normalized_max_tokens != getattr(setting, "max_tokens", None):
                setting.max_tokens = normalized_max_tokens
                should_save = True
            normalized_theme = self.normalize_theme_mode(getattr(setting, "theme_mode", None))
            if normalized_theme != getattr(setting, "theme_mode", None):
                setting.theme_mode = normalized_theme
                should_save = True
            if should_save:
                setting = self._save_setting(setting)
        return self._to_response(setting)

    def update_user_settings(self, user_id: str, payload: UserSettingUpdate) -> UserSettingResponse:
        setting = self.repo.get_by_user(user_id)
        if not setting:
            setting = self._build_default_setting(user_id)

        data = payload.model_dump(exclude_unset=True)
        previous_provider_type = setting.provider_type
        # 普通字符串先做 trim；空字符串归一为 None，避免把无意义空值写进配置。
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
            # 密钥字段不能走普通 setattr，否则会把明文直接写进数据库。
            if key in {
                "api_key",
                "clear_api_key",
                "knowledge_embedding_api_key",
                "clear_knowledge_embedding_api_key",
                "knowledge_rerank_api_key",
                "clear_knowledge_rerank_api_key",
            }:
                continue
            setattr(setting, key, value)

        provider_changed = bool(
            data.get("provider_type") and data["provider_type"] != previous_provider_type
        )
        if data.get("clear_api_key") or (provider_changed and not data.get("api_key")):
            # 一个字段不能安全表示多个 Provider 的凭据。切换服务时默认清空旧 Key，
            # 防止把在线服务密钥发送给新配置的本地或第三方 Base URL。
            setting.api_key = None
        elif "api_key" in data and data["api_key"] is not None:
            # 用户级模型 API Key 只加密保存；响应阶段不会回显明文。
            setting.api_key = self.secrets.encrypt(data["api_key"])

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
        if provider_changed and "default_model" not in data:
            setting.default_model = self.default_model_for_provider(setting.provider_type)
        if not getattr(setting, "ollama_base_url", None):
            setting.ollama_base_url = app_settings.ollama_base_url
        if not getattr(setting, "api_base_url", None):
            setting.api_base_url = self.default_api_base_url_for_provider(setting.provider_type)
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
        setting.max_tokens = self.normalize_max_tokens(getattr(setting, "max_tokens", None))
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

        saved = self._save_setting(setting)
        return self._to_response(saved)

    def resolve_provider_api_key(self, user_id: str) -> str | None:
        setting = self.repo.get_by_user(user_id)
        if not setting:
            return None
        return self.secrets.decrypt(getattr(setting, "api_key", None))

    def resolve_knowledge_model_api_key(self, user_id: str, model_kind: str) -> str | None:
        setting = self.repo.get_by_user(user_id)
        if not setting:
            return None
        if model_kind == "rerank":
            return self.secrets.decrypt(getattr(setting, "knowledge_rerank_api_key", None))
        return self.secrets.decrypt(getattr(setting, "knowledge_embedding_api_key", None))

    def _to_response(self, setting: UserSetting) -> UserSettingResponse:
        # 出站前只解密用于判断/掩码，绝不把密钥明文放进响应。
        raw_api_key = self.secrets.decrypt(getattr(setting, "api_key", None))
        raw_knowledge_embedding_api_key = self.secrets.decrypt(
            getattr(setting, "knowledge_embedding_api_key", None)
        )
        raw_knowledge_rerank_api_key = self.secrets.decrypt(
            getattr(setting, "knowledge_rerank_api_key", None)
        )
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
                "knowledge_embedding_has_api_key": bool(raw_knowledge_embedding_api_key),
                "knowledge_embedding_api_key_masked": self.secrets.mask(raw_knowledge_embedding_api_key),
                "knowledge_rerank_api_key": None,
                "knowledge_rerank_has_api_key": bool(raw_knowledge_rerank_api_key),
                "knowledge_rerank_api_key_masked": self.secrets.mask(raw_knowledge_rerank_api_key),
                "updated_at": setting.updated_at,
            }
        )
