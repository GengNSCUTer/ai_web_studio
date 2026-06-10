import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


ROOT_DIR = Path(__file__).resolve().parents[2]
load_dotenv(ROOT_DIR / ".env")


@dataclass(frozen=True)
class Settings:
    app_name: str = os.getenv("APP_NAME", "ai_web_studio_backend")
    app_env: str = os.getenv("APP_ENV", "development")
    app_host: str = os.getenv("APP_HOST", "127.0.0.1")
    app_port: int = int(os.getenv("APP_PORT", "32007"))

    postgres_host: str = os.getenv("POSTGRES_HOST", "127.0.0.1")
    postgres_port: int = int(os.getenv("POSTGRES_PORT", "35432"))
    postgres_db: str = os.getenv("POSTGRES_DB", "ai_web_studio")
    postgres_user: str = os.getenv("POSTGRES_USER", "ligengnan")
    postgres_password: str = os.getenv("POSTGRES_PASSWORD", "")

    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11435")
    ollama_default_model: str = os.getenv("OLLAMA_DEFAULT_MODEL", "qwen3.5:27b-q8_0")
    ollama_keep_alive: str = os.getenv("OLLAMA_KEEP_ALIVE", "30m")
    ollama_request_timeout_seconds: int = int(os.getenv("OLLAMA_REQUEST_TIMEOUT_SECONDS", "600"))

    auth_secret_key: str = os.getenv(
        "AUTH_SECRET_KEY",
        "change-this-before-production-ai-web-studio-secret",
    )
    auth_algorithm: str = os.getenv("AUTH_ALGORITHM", "HS256")
    auth_access_token_expire_minutes: int = int(
        os.getenv("AUTH_ACCESS_TOKEN_EXPIRE_MINUTES", "10080")
    )

    upload_dir: str = os.getenv("UPLOAD_DIR", str(ROOT_DIR / "uploads"))
    knowledge_index_dir: str = os.getenv("KNOWLEDGE_INDEX_DIR", str(ROOT_DIR / "uploads" / "knowledge_indexes"))
    knowledge_parse_max_chars: int = int(os.getenv("KNOWLEDGE_PARSE_MAX_CHARS", "500000"))

    tavily_api_key: str = os.getenv("TAVILY_API_KEY", "")
    amap_api_key: str = os.getenv("AMAP_API_KEY", "")
    mineru_api_token: str = os.getenv("MINERU_API_TOKEN", "")
    mineru_poll_timeout_seconds: int = int(os.getenv("MINERU_POLL_TIMEOUT_SECONDS", "300"))
    mineru_poll_interval_seconds: float = float(os.getenv("MINERU_POLL_INTERVAL_SECONDS", "3"))
    external_tool_timeout_seconds: int = int(os.getenv("EXTERNAL_TOOL_TIMEOUT_SECONDS", "8"))
    secret_encryption_key: str = os.getenv("SECRET_ENCRYPTION_KEY", "")

    @property
    def sqlalchemy_database_uri(self) -> str:
        auth = self.postgres_user
        if self.postgres_password:
            auth = f"{auth}:{self.postgres_password}"
        return (
            f"postgresql+psycopg://{auth}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


settings = Settings()
