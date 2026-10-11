"""检查账号的公共 MCP 凭据和真实工具调用，只输出状态及来源数量。

显式传入 --configure-from-env 时才将 .env 的两项 Key 保存到固定账号；
不打开全局凭据共享，不打印令牌、Key、请求头或远端异常正文。
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import sys

import httpx
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.config import settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.user import User  # noqa: E402
from app.repositories.user_repo import UserRepository  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402
from app.services.tools.credentials import ToolCredentialResolver  # noqa: E402
from app.services.tools.executor import ToolExecutor  # noqa: E402
from app.services.tools.schemas import PlannedToolCall  # noqa: E402


class PublicCredentialCheckError(RuntimeError):
    """只用于本脚本生成的固定诊断，不包含远端原始响应或凭据。"""


async def run(*, configure: bool) -> None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "1528713326@qq.com"))
        if not user:
            raise PublicCredentialCheckError("固定测试账号不存在。")
        user_id = user.id
        token = AuthService(UserRepository(db)).create_access_token(user)
    keys = {"exa": settings.exa_api_key.strip(), "context7": settings.context7_api_key.strip()}
    base_url = "http://127.0.0.1:32017"
    report = {"env_sharing_enabled": settings.allow_env_tool_credential_fallback, "connections": [], "tools": []}
    async with httpx.AsyncClient(base_url=base_url, headers={"Authorization": "Bearer " + token}, timeout=60) as client:
        for provider in keys:
            if configure:
                if not keys[provider]:
                    raise PublicCredentialCheckError(f"{provider} 的环境 Key 未配置。")
                saved = await client.patch(f"/api/tools/credentials/{provider}", json={
                    "api_key": keys[provider], "is_enabled": True,
                })
                if saved.status_code != 200 or not saved.json().get("has_api_key"):
                    raise PublicCredentialCheckError(f"{provider} 凭据保存失败，HTTP {saved.status_code}。")
            tested = await client.post(f"/api/tools/credentials/{provider}/test")
            if tested.status_code != 200 or not tested.json().get("ok"):
                raise PublicCredentialCheckError(f"{provider} 真实连接测试失败，HTTP {tested.status_code}。")
            report["connections"].append({"provider": provider, "http_status": tested.status_code, "ok": True})

    url = "https://docs.sqlalchemy.org/en/20/orm/session_basics.html"
    calls = [
        ("web.exa.search", {"query": "SQLAlchemy Session official documentation", "objective": "Find official SQLAlchemy Session documentation", "numResults": 2}),
        ("web.exa.fetch", {"urls": [url], "maxCharacters": 8000}),
        ("docs.context7.resolve", {"libraryName": "SQLAlchemy", "query": "SQLAlchemy 2.0 Session flush and commit"}),
    ]
    library_id = None
    for key, arguments in [*calls, ("docs.context7.query", None)]:
        if arguments is None:
            if not library_id:
                raise PublicCredentialCheckError("真实库定位未返回 ID，不能猜测文档查询参数。")
            arguments = {"libraryId": library_id, "query": "Session.flush versus Session.commit and subsequent autobegin", "language": "Python"}
        with SessionLocal() as db:
            resolved = ToolCredentialResolver(db).resolve(user_id=user_id, provider_key="exa" if key.startswith("web.") else "context7")
            if resolved.source != "user" or not resolved.api_key:
                raise PublicCredentialCheckError("本次必须验证账号凭据，不能用匿名调用冒充 Key 验证。")
            executor = ToolExecutor(db=db, user_id=user_id)
            definition = executor.catalog.get(key)
            call = PlannedToolCall(call_id="credential_smoke_" + key, tool_key=key, provider=definition.provider,
                                   category=definition.category, display_name=definition.display_name,
                                   confidence=1.0, reason="用户授权的真实凭据测试", arguments=arguments)
            result, events = await executor.execute(call)
            if result.status != "success" or result.quality_status != "valid" or not result.sources:
                raise PublicCredentialCheckError(f"{key} 未取得有效证据：{result.status}/{result.quality_status}。")
            if not any(event.payload.get("credential_source") == "user" for event in events):
                raise PublicCredentialCheckError("执行轨迹未确认使用账号凭据。")
            serialized = json.dumps({"result": asdict(result), "events": [asdict(event) for event in events]}, ensure_ascii=False)
            if any(secret and secret in serialized for secret in keys.values()):
                raise PublicCredentialCheckError("凭据出现在工具结果或轨迹中。")
            if key == "docs.context7.resolve":
                library_id = result.sources[0].metadata["raw"]["library_id"]
            report["tools"].append({"tool_key": key, "credential_source": "user", "status": result.status,
                                    "quality": result.quality_status, "sources": len(result.sources), "elapsed_ms": result.elapsed_ms})
    print(json.dumps(report, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="真实验证公共 MCP 账号凭据，不打印敏感内容。")
    parser.add_argument("--configure-from-env", action="store_true", help="显式将环境 Key 加密保存到固定测试账号")
    args = parser.parse_args()
    try:
        asyncio.run(run(configure=args.configure_from_env))
    except Exception as exc:
        # 仅输出脚本自身的固定诊断；远端 RuntimeError 也可能含敏感正文。
        print(json.dumps({"ok": False, "error_type": type(exc).__name__,
                          "reason": str(exc) if isinstance(exc, PublicCredentialCheckError) else "第三方或网络调用异常"}, ensure_ascii=False))
        raise SystemExit(1) from None
