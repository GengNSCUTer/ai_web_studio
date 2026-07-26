# 系统环境变量读取模块
import os
# 不可变数据类，用于存放全局配置参数
from dataclasses import dataclass
# 路径处理工具
from pathlib import Path

# 加载.env环境变量文件工具
from dotenv import load_dotenv

# 获取项目根目录：当前文件向上两级目录
ROOT_DIR = Path(__file__).resolve().parents[2]
# 加载项目根目录下的.env配置文件，读取自定义环境变量
load_dotenv(ROOT_DIR / ".env")

DEFAULT_AUTH_SECRET_KEY = "change-this-before-production-ai-web-studio-secret"


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_csv(name: str, default: str) -> tuple[str, ...]:
    value = os.getenv(name, default)
    return tuple(item.strip() for item in value.split(",") if item.strip())


# 冻结数据类，实例化后参数不可修改，存放所有项目配置
@dataclass(frozen=True)
class Settings:
    # 应用名称，读取环境变量APP_NAME，默认ai_web_studio_backend
    app_name: str = os.getenv("APP_NAME", "ai_web_studio_backend")
    # 运行环境：development/production/test
    app_env: str = os.getenv("APP_ENV", "development")
    # 服务监听地址
    app_host: str = os.getenv("APP_HOST", "127.0.0.1")
    # 服务启动端口
    app_port: int = int(os.getenv("APP_PORT", "32007"))
    cors_allowed_origins: tuple[str, ...] = _env_csv(
        "CORS_ALLOWED_ORIGINS",
        "http://127.0.0.1:32008,http://localhost:32008,http://127.0.0.1:3000,http://localhost:3000",
    )

    # Postgres数据库地址
    postgres_host: str = os.getenv("POSTGRES_HOST", "127.0.0.1")
    # Postgres数据库端口
    postgres_port: int = int(os.getenv("POSTGRES_PORT", "35433"))
    # 数据库库名
    postgres_db: str = os.getenv("POSTGRES_DB", "ai_web_studio")
    # 数据库登录用户名
    postgres_user: str = os.getenv("POSTGRES_USER", "ligengnan")
    # 数据库登录密码
    postgres_password: str = os.getenv("POSTGRES_PASSWORD", "")

    # Ollama本地大模型服务接口地址
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11435")
    # 默认使用的Ollama模型名称
    ollama_default_model: str = os.getenv("OLLAMA_DEFAULT_MODEL", "qwen3.5:27b-q8_0")
    # 模型加载后常驻内存时长
    ollama_keep_alive: str = os.getenv("OLLAMA_KEEP_ALIVE", "30m")
    # Ollama接口请求超时秒数
    ollama_request_timeout_seconds: int = int(os.getenv("OLLAMA_REQUEST_TIMEOUT_SECONDS", "600"))
    # Chat 流式请求的三层超时：首事件、相邻事件闲置、整次模型生成总时长。
    chat_first_token_timeout_seconds: float = float(os.getenv("CHAT_FIRST_TOKEN_TIMEOUT_SECONDS", "120"))
    chat_stream_idle_timeout_seconds: float = float(os.getenv("CHAT_STREAM_IDLE_TIMEOUT_SECONDS", "120"))
    chat_stream_total_timeout_seconds: float = float(os.getenv("CHAT_STREAM_TOTAL_TIMEOUT_SECONDS", "900"))

    # JWT鉴权加密密钥，生产环境必须替换
    auth_secret_key: str = os.getenv(
        "AUTH_SECRET_KEY",
        DEFAULT_AUTH_SECRET_KEY,
    )
    # JWT加密算法
    auth_algorithm: str = os.getenv("AUTH_ALGORITHM", "HS256")
    # 登录访问令牌过期时长（分钟）
    auth_access_token_expire_minutes: int = int(
        os.getenv("AUTH_ACCESS_TOKEN_EXPIRE_MINUTES", "10080")
    )

    # 文件上传存储根目录
    upload_dir: str = os.getenv("UPLOAD_DIR", str(ROOT_DIR / "uploads"))
    # 知识库向量索引文件存放目录
    knowledge_index_dir: str = os.getenv("KNOWLEDGE_INDEX_DIR", str(ROOT_DIR / "uploads" / "knowledge_indexes"))
    # 单份文档解析最大字符上限
    knowledge_parse_max_chars: int = int(os.getenv("KNOWLEDGE_PARSE_MAX_CHARS", "500000"))
    # 知识库向量化模型请求超时秒数
    knowledge_model_request_timeout_seconds: int = int(os.getenv("KNOWLEDGE_MODEL_REQUEST_TIMEOUT_SECONDS", "60"))
    # 知识库检索上下文加载超时秒数
    knowledge_context_timeout_seconds: int = int(os.getenv("KNOWLEDGE_CONTEXT_TIMEOUT_SECONDS", "25"))
    # 知识库后台任务：HTTP 只写 PostgreSQL Outbox，独立 Worker 通过 Redis Stream 消费。
    knowledge_redis_url: str = os.getenv("KNOWLEDGE_REDIS_URL", "redis://127.0.0.1:6379/0")
    knowledge_job_stream: str = os.getenv("KNOWLEDGE_JOB_STREAM", "stream.aiws.knowledge-jobs")
    knowledge_job_group: str = os.getenv("KNOWLEDGE_JOB_GROUP", "aiws-knowledge-workers")
    knowledge_job_lease_seconds: int = int(os.getenv("KNOWLEDGE_JOB_LEASE_SECONDS", "90"))
    knowledge_job_claim_idle_ms: int = int(os.getenv("KNOWLEDGE_JOB_CLAIM_IDLE_MS", "90000"))
    knowledge_job_max_attempts: int = int(os.getenv("KNOWLEDGE_JOB_MAX_ATTEMPTS", "3"))

    # Tavily联网搜索工具API密钥
    tavily_api_key: str = os.getenv("TAVILY_API_KEY", "")
    # 高德地图API密钥
    amap_api_key: str = os.getenv("AMAP_API_KEY", "")
    # MinerU PDF解析服务token
    mineru_api_token: str = os.getenv("MINERU_API_TOKEN", "")
    # 是否允许用户未配置凭据时回退使用服务端.env里的工具凭据；多用户生产环境建议关闭
    allow_env_tool_credential_fallback: bool = _env_bool("ALLOW_ENV_TOOL_CREDENTIAL_FALLBACK", False)
    # MinerU异步任务轮询总超时秒数
    mineru_poll_timeout_seconds: int = int(os.getenv("MINERU_POLL_TIMEOUT_SECONDS", "300"))
    # MinerU任务状态轮询间隔（秒，支持小数）
    mineru_poll_interval_seconds: float = float(os.getenv("MINERU_POLL_INTERVAL_SECONDS", "3"))
    # 第三方外部工具通用请求超时秒数
    external_tool_timeout_seconds: int = int(os.getenv("EXTERNAL_TOOL_TIMEOUT_SECONDS", "8"))
    # 单次 MCP JSON/SSE 响应最大字节数，防止不可信 Server 用超大响应耗尽内存或撑爆 Trace。
    mcp_max_response_bytes: int = int(os.getenv("MCP_MAX_RESPONSE_BYTES", str(1024 * 1024)))
    # 默认禁止用户添加的 MCP Server 访问回环、私网和云元数据地址；本地单用户部署可显式开启。
    allow_private_mcp_servers: bool = _env_bool("ALLOW_PRIVATE_MCP_SERVERS", False)
    # 用户存储密钥等敏感信息的加密密钥
    secret_encryption_key: str = os.getenv("SECRET_ENCRYPTION_KEY", "")

    # 只读属性：拼接生成SQLAlchemy数据库连接字符串
    @property
    def sqlalchemy_database_uri(self) -> str:
        auth = self.postgres_user
        # 存在密码则拼接账号:密码格式
        if self.postgres_password:
            auth = f"{auth}:{self.postgres_password}"
        # 组装完整postgres连接地址
        return (
            f"postgresql+psycopg://{auth}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

# 全局单例配置对象，项目各处直接导入使用
settings = Settings()


def validate_runtime_security_settings(config: Settings = settings) -> None:
    """Fail fast instead of starting production with a known JWT signing key."""

    if config.app_env != "production":
        return
    secret = config.auth_secret_key.strip()
    if secret in {"", DEFAULT_AUTH_SECRET_KEY, "change-this-to-a-long-random-string"} or len(secret) < 32:
        raise RuntimeError("生产环境必须配置至少 32 个字符的独立 AUTH_SECRET_KEY")
