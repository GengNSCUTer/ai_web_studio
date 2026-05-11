from collections.abc import AsyncGenerator
from typing import Any

import httpx
from openai import AsyncOpenAI

from app.core.config import settings


class ChatProviderService:
    async def list_models(
        self,
        *,
        provider_type: str,
        base_url: str,
        api_key: str | None,
    ) -> list[str]:
        if provider_type == "ollama":
            from app.services.ollama_service import OllamaService

            return await OllamaService(base_url=base_url).list_models()

        if provider_type == "openai-compatible":
            client = AsyncOpenAI(
                api_key=api_key or "sk-placeholder",
                base_url=base_url,
            )
            response = await client.models.list()
            return [item.id for item in response.data]

        return []

    async def stream_chat(
        self,
        *,
        provider_type: str,
        base_url: str,
        api_key: str | None,
        model_name: str,
        messages: list[dict[str, Any]],
        temperature: float,
        top_p: float,
        max_tokens: int | None,
    ) -> AsyncGenerator[str, None]:
        if provider_type == "ollama":
            from app.services.ollama_service import OllamaService

            async for chunk in OllamaService(base_url=base_url).stream_chat(
                model_name=model_name,
                messages=messages,
            ):
                yield chunk
            return

        if provider_type != "openai-compatible":
            raise ValueError(f"Unsupported provider_type: {provider_type}")

        client = AsyncOpenAI(
            api_key=api_key or "sk-placeholder",
            base_url=base_url,
        )
        try:
            stream = await client.chat.completions.create(
                model=model_name,
                messages=messages,
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
                stream=True,
                extra_body={"enable_thinking": False},
            )

            async for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                content = delta.content if delta else None
                if content:
                    yield content
        except httpx.TimeoutException as exc:
            raise RuntimeError("在线模型响应超时，请稍后重试") from exc

    async def complete_chat(
        self,
        *,
        provider_type: str,
        base_url: str,
        api_key: str | None,
        model_name: str,
        messages: list[dict[str, Any]],
        temperature: float = 0.2,
        top_p: float = 0.9,
        max_tokens: int | None = None,
    ) -> str:
        if provider_type == "ollama":
            from app.services.ollama_service import OllamaService

            return await OllamaService(base_url=base_url).complete_chat(
                model_name=model_name,
                messages=messages,
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
            )

        if provider_type != "openai-compatible":
            raise ValueError(f"Unsupported provider_type: {provider_type}")

        client = AsyncOpenAI(
            api_key=api_key or "sk-placeholder",
            base_url=base_url,
        )
        try:
            response = await client.chat.completions.create(
                model=model_name,
                messages=messages,
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
                stream=False,
                extra_body={"enable_thinking": False},
            )
        except httpx.TimeoutException as exc:
            raise RuntimeError("在线模型响应超时，请稍后重试") from exc

        if not response.choices:
            return ""
        return (response.choices[0].message.content or "").strip()


def resolve_provider_base_url(
    *,
    provider_type: str,
    configured_base_url: str | None,
) -> str:
    if configured_base_url:
        return configured_base_url

    if provider_type == "ollama":
        return settings.ollama_base_url

    return ""
