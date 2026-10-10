"""工作区文件的稳定翻页位置；它只定位下一页，不授予访问权限。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime

from app.core.config import settings
from app.services.tools.schemas import ToolExecutionFeedbackError


class WorkspaceFileCursor:
    MAX_LENGTH = 160

    @staticmethod
    def scope(*, user_id: str, project_id: str, operation: str, arguments: dict) -> str:
        # 继续同一查询时才接受位置；不能拿另一用户、项目或搜索条件的游标来翻页。
        values = [user_id, project_id, operation, arguments.get("query", ""),
                  arguments.get("file_name", ""), arguments.get("file_id", "")]
        return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()[:16]

    @staticmethod
    def _signature(payload: str) -> str:
        key = f"workspace-file-cursor-v1:{settings.auth_secret_key}".encode()
        digest = hmac.new(key, payload.encode(), hashlib.sha256).digest()[:16]
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")

    @classmethod
    def encode(cls, *, created_at: datetime, file_id: str, scope: str) -> str:
        data = json.dumps([created_at.isoformat(), file_id, scope], separators=(",", ":")).encode()
        payload = base64.urlsafe_b64encode(data).decode().rstrip("=")
        token = f"{payload}.{cls._signature(payload)}"
        # Planner 的受限事实字段最多 160 字符，不能返回会被截断而无法使用的游标。
        if len(token) > cls.MAX_LENGTH:
            raise ToolExecutionFeedbackError("文件分页位置超出支持范围。")
        return token

    @classmethod
    def decode(cls, token: str, *, scope: str) -> tuple[datetime, str]:
        try:
            if not isinstance(token, str) or not token or len(token) > cls.MAX_LENGTH:
                raise ValueError
            payload, signature = token.split(".")
            if not hmac.compare_digest(signature, cls._signature(payload)):
                raise ValueError
            data = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            if not isinstance(data, list) or len(data) != 3 or data[2] != scope:
                raise ValueError
            created_at, file_id, _ = data
            if not isinstance(file_id, str) or not 1 <= len(file_id) <= 36:
                raise ValueError
            return datetime.fromisoformat(created_at), file_id
        except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError):
            raise ToolExecutionFeedbackError("文件分页位置无效，或与当前用户、项目、查询条件不一致；请从第一页重新查询。") from None
