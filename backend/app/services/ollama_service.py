import json
from collections.abc import AsyncGenerator

import httpx

from app.core.config import settings


class OllamaService:
    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")

    async def list_models(self) -> list[str]:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(f"{self.base_url}/api/tags")
            response.raise_for_status()
            payload = response.json()
        return [item["name"] for item in payload.get("models", [])]

    async def stream_chat(
        self,
        *,
        model_name: str,
        messages: list[dict[str, str]],
    ) -> AsyncGenerator[str, None]:
        payload = {
            "model": model_name,
            "messages": messages,
            "stream": True,
            "think": False,
            "keep_alive": settings.ollama_keep_alive,
        }

        async with httpx.AsyncClient(timeout=settings.ollama_request_timeout_seconds) as client:
            try:
                async with client.stream(
                    "POST",
                    f"{self.base_url}/api/chat",
                    json=payload,
                ) as response:
                    try:
                        response.raise_for_status()
                    except httpx.HTTPStatusError as exc:
                        if exc.response.status_code == 404:
                            raise RuntimeError(f"模型不存在：{model_name}") from exc
                        raise RuntimeError("Ollama 推理失败，请检查模型服务状态") from exc

                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        chunk = data.get("message", {}).get("content", "")
                        if chunk:
                            yield chunk
                        if data.get("done"):
                            break
            except httpx.ConnectError as exc:
                raise RuntimeError("无法连接到 Ollama 服务，请检查地址和服务状态") from exc
            except httpx.TimeoutException as exc:
                raise RuntimeError("Ollama 响应超时，请稍后重试") from exc
