from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


class SecretService:
    """对用户级密钥做加密、解密和掩码展示，避免设置响应回显明文。"""

    PREFIX = "enc::"

    def __init__(self, secret: str | None = None) -> None:
        # 生产环境必须使用独立 SECRET_ENCRYPTION_KEY；开发环境才允许 fallback 到 AUTH_SECRET_KEY。
        configured_secret = (secret or settings.secret_encryption_key).strip()
        if not configured_secret:
            if settings.app_env == "production":
                raise RuntimeError("生产环境必须配置 SECRET_ENCRYPTION_KEY")
            configured_secret = settings.auth_secret_key.strip()
        self.secret = configured_secret
        digest = hashlib.sha256(self.secret.encode("utf-8")).digest()
        self.fernet = Fernet(base64.urlsafe_b64encode(digest))

    def encrypt(self, value: str | None) -> str | None:
        normalized = (value or "").strip()
        if not normalized:
            return None
        token = self.fernet.encrypt(normalized.encode("utf-8")).decode("utf-8")
        return f"{self.PREFIX}{token}"

    def decrypt(self, value: str | None) -> str | None:
        normalized = (value or "").strip()
        if not normalized:
            return None
        if not normalized.startswith(self.PREFIX):
            # Backward compatibility for legacy plaintext rows.
            return normalized
        token = normalized[len(self.PREFIX) :]
        try:
            return self.fernet.decrypt(token.encode("utf-8")).decode("utf-8")
        except InvalidToken:
            return None

    @staticmethod
    def mask(value: str | None) -> str | None:
        normalized = (value or "").strip()
        if not normalized:
            return None
        if len(normalized) <= 8:
            return "****"
        return f"{normalized[:4]}****{normalized[-4:]}"
