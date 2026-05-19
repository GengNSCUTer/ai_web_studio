from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import Any

import httpx
from openai import AsyncOpenAI

from app.core.config import settings


@dataclass(frozen=True)
class ChatStreamEvent:
    type: str
    text: str


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
        async for event in self.stream_chat_events(
            provider_type=provider_type,
            base_url=base_url,
            api_key=api_key,
            model_name=model_name,
            messages=messages,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            thinking_enabled=False,
            thinking_budget=None,
        ):
            if event.type == "answer_delta":
                yield event.text

    async def stream_chat_events(
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
        thinking_enabled: bool = False,
        thinking_budget: int | None = None,
    ) -> AsyncGenerator[ChatStreamEvent, None]:
        if provider_type == "ollama":
            from app.services.ollama_service import OllamaService

            async for event in OllamaService(base_url=base_url).stream_chat_events(
                model_name=model_name,
                messages=messages,
                thinking_enabled=thinking_enabled,
            ):
                yield ChatStreamEvent(type=event.type, text=event.text)
            return

        if provider_type != "openai-compatible":
            raise ValueError(f"Unsupported provider_type: {provider_type}")

        client = AsyncOpenAI(
            api_key=api_key or "sk-placeholder",
            base_url=base_url,
        )
        try:
            extra_body = self._build_openai_extra_body(
                base_url=base_url,
                model_name=model_name,
                thinking_enabled=thinking_enabled,
                thinking_budget=thinking_budget,
            )
            stream = await client.chat.completions.create(
                model=model_name,
                messages=messages,
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
                stream=True,
                extra_body=extra_body,
            )

            async for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                reasoning = self._extract_delta_attr(delta, "reasoning_content")
                if reasoning:
                    yield ChatStreamEvent(type="reasoning_delta", text=reasoning)
                content = delta.content if delta else None
                if content:
                    yield ChatStreamEvent(type="answer_delta", text=content)
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
            extra_body = self._build_openai_extra_body(
                base_url=base_url,
                model_name=model_name,
                thinking_enabled=False,
                thinking_budget=None,
            )
            response = await client.chat.completions.create(
                model=model_name,
                messages=messages,
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
                stream=False,
                extra_body=extra_body,
            )
        except httpx.TimeoutException as exc:
            raise RuntimeError("在线模型响应超时，请稍后重试") from exc

        if not response.choices:
            return ""
        return (response.choices[0].message.content or "").strip()

    @staticmethod
    def _build_openai_extra_body(
        *,
        base_url: str,
        model_name: str,
        thinking_enabled: bool,
        thinking_budget: int | None,
    ) -> dict[str, Any]:
        normalized = f"{base_url} {model_name}".lower()
        supports_qwen_thinking = any(
            marker in normalized
            for marker in ("siliconflow", "qwen3", "qwen/qwen3", "deepseek-r1")
        )
        if not supports_qwen_thinking:
            return {}

        extra_body: dict[str, Any] = {"enable_thinking": thinking_enabled}
        if thinking_enabled and thinking_budget:
            extra_body["thinking_budget"] = thinking_budget
        return extra_body

    @staticmethod
    def _extract_delta_attr(delta: Any, name: str) -> str | None:
        if delta is None:
            return None
        value = getattr(delta, name, None)
        if value:
            return str(value)
        if isinstance(delta, dict):
            value = delta.get(name) or delta.get("thinking")
            if value:
                return str(value)
        return None


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
