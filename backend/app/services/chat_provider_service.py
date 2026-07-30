from collections.abc import AsyncGenerator
import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
from typing import Any

import httpx
from openai import AsyncOpenAI

from app.core.config import settings
from app.services.provider_capabilities import resolve_provider_capabilities


OPENAI_COMPATIBLE_PROVIDER_TYPES = frozenset({"openai-compatible", "vllm"})


@dataclass(frozen=True)
class ChatStreamEvent:
    """Provider 返回的统一流式事件。

    不同供应商的字段不同，服务内统一成 reasoning_delta / answer_delta，
    上层 route 就不用关心 Ollama 或 OpenAI-compatible 的差异。
    """

    type: str
    text: str = ""
    data: dict[str, Any] | None = None


class ChatProviderService:
    """模型供应商适配层。

    这一层只负责和模型服务通信：列模型、流式聊天、一次性补全。
    它不应该知道用户权限、会话归属、RAG、工具调用或上下文预算。
    """

    async def list_models(
        self,
        *,
        provider_type: str,
        base_url: str,
        api_key: str | None,
    ) -> list[str]:
        # 设置页测试连接会调用这里。Ollama 和 OpenAI-compatible 的模型列表协议不同。
        if provider_type == "ollama":
            from app.services.ollama_service import OllamaService

            return await OllamaService(base_url=base_url).list_models()

        if provider_type in OPENAI_COMPATIBLE_PROVIDER_TYPES:
            client = AsyncOpenAI(
                api_key=api_key or "sk-placeholder",
                base_url=base_url,
            )
            try:
                response = await client.models.list()
            finally:
                await client.close()
            return [item.id for item in response.data]

        if provider_type == "anthropic":
            payload = await self._anthropic_json_request(
                method="GET",
                base_url=base_url,
                api_key=api_key,
                path="models",
            )
            return [str(item.get("id")) for item in payload.get("data", []) if item.get("id")]

        raise ValueError(f"Unsupported provider_type: {provider_type}")

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
        prompt_cache_key: str | None = None,
        prompt_cache_breakpoint: int | None = None,
    ) -> AsyncGenerator[str, None]:
        # 兼容旧的纯文本流入口：只把 answer_delta 文本吐出去，忽略 reasoning_delta。
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
            prompt_cache_key=prompt_cache_key,
            prompt_cache_breakpoint=prompt_cache_breakpoint,
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
        prompt_cache_key: str | None = None,
        prompt_cache_breakpoint: int | None = None,
    ) -> AsyncGenerator[ChatStreamEvent, None]:
        # 新主路径：保留 reasoning_delta，供前端展示“深度思考”折叠面板。
        if provider_type == "ollama":
            from app.services.ollama_service import OllamaService

            async for event in OllamaService(base_url=base_url).stream_chat_events(
                model_name=model_name,
                messages=messages,
                thinking_enabled=thinking_enabled,
            ):
                yield ChatStreamEvent(type=event.type, text=event.text)
            return

        if provider_type == "anthropic":
            async for event in self._stream_anthropic(
                base_url=base_url,
                api_key=api_key,
                model_name=model_name,
                messages=messages,
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
                thinking_enabled=thinking_enabled,
                thinking_budget=thinking_budget,
                prompt_cache_breakpoint=prompt_cache_breakpoint,
            ):
                yield event
            return

        if provider_type not in OPENAI_COMPATIBLE_PROVIDER_TYPES:
            raise ValueError(f"Unsupported provider_type: {provider_type}")

        client = AsyncOpenAI(
            api_key=api_key or "sk-placeholder",
            base_url=base_url,
        )
        try:
            capabilities = resolve_provider_capabilities(
                provider_type=provider_type,
                base_url=base_url,
            )
            extra_body = self._build_openai_extra_body(
                base_url=base_url,
                model_name=model_name,
                thinking_enabled=thinking_enabled,
                thinking_budget=thinking_budget,
            )
            create_kwargs: dict[str, Any] = {
                "model": model_name,
                "messages": messages,
                "temperature": temperature,
                "top_p": top_p,
                "max_tokens": max_tokens,
                "stream": True,
                "extra_body": extra_body,
            }
            if capabilities.request_stream_usage:
                create_kwargs["stream_options"] = {"include_usage": True}
            cache_key_sent = False
            if prompt_cache_key and capabilities.request_cache_key_field:
                request_cache_key = prompt_cache_key
                if capabilities.family == "vllm":
                    request_cache_key = self._build_vllm_cache_salt(prompt_cache_key)
                if capabilities.request_cache_key_in_extra_body:
                    extra_body[capabilities.request_cache_key_field] = request_cache_key
                else:
                    create_kwargs[capabilities.request_cache_key_field] = request_cache_key
                cache_key_sent = True
            stream = await client.chat.completions.create(
                **create_kwargs,
            )

            async for chunk in stream:
                delta = chunk.choices[0].delta if chunk.choices else None
                # 兼容硅基流动/Qwen/DeepSeek-R1 等 OpenAI-compatible 扩展字段。
                reasoning = self._extract_delta_attr(delta, "reasoning_content")
                if reasoning:
                    yield ChatStreamEvent(type="reasoning_delta", text=reasoning)
                content = delta.content if delta else None
                if content:
                    yield ChatStreamEvent(type="answer_delta", text=content)
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    details = getattr(usage, "prompt_tokens_details", None)
                    cached_tokens = self._optional_int_attr(details, "cached_tokens")
                    if cached_tokens is None:
                        cached_tokens = self._optional_int_attr(usage, "prompt_cache_hit_tokens")
                    yield ChatStreamEvent(
                        type="provider_usage",
                        data={
                            "provider": capabilities.family,
                            "input_tokens": int(self._optional_int_attr(usage, "prompt_tokens") or 0),
                            "output_tokens": int(self._optional_int_attr(usage, "completion_tokens") or 0),
                            "cached_input_tokens": int(cached_tokens or 0),
                            "prompt_cache_mode": capabilities.prompt_cache_mode,
                            "prompt_cache_request_key_sent": cache_key_sent,
                            "prompt_cache_usage_available": cached_tokens is not None,
                            "prompt_cache_usage_support": capabilities.cache_usage_support,
                        },
                    )
        except httpx.TimeoutException as exc:
            raise RuntimeError("模型服务响应超时，请稍后重试") from exc
        finally:
            await client.close()

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
        prompt_cache_breakpoint: int | None = None,
    ) -> str:
        # 非流式补全当前主要用于滚动摘要刷新，不直接服务前端聊天输出。
        if provider_type == "ollama":
            from app.services.ollama_service import OllamaService

            return await OllamaService(base_url=base_url).complete_chat(
                model_name=model_name,
                messages=messages,
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
            )

        if provider_type == "anthropic":
            payload = self._build_anthropic_payload(
                model_name=model_name,
                messages=messages,
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
                stream=False,
                thinking_enabled=False,
                thinking_budget=None,
                prompt_cache_breakpoint=prompt_cache_breakpoint,
            )
            response = await self._anthropic_json_request(
                method="POST",
                base_url=base_url,
                api_key=api_key,
                path="messages",
                json_body=payload,
            )
            return "".join(
                str(block.get("text") or "")
                for block in response.get("content", [])
                if block.get("type") == "text"
            ).strip()

        if provider_type not in OPENAI_COMPATIBLE_PROVIDER_TYPES:
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
            raise RuntimeError("模型服务响应超时，请稍后重试") from exc
        finally:
            await client.close()

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
        # “深度思考”不是 OpenAI 标准字段，不同供应商字段不同。
        # 当前只给已知兼容 enable_thinking/thinking_budget 的模型或服务附加 extra_body。
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
        # OpenAI SDK 返回对象；部分兼容服务可能返回 dict-like delta。
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

    @staticmethod
    def _optional_int_attr(value: Any, name: str) -> int | None:
        if value is None:
            return None
        raw = value.get(name) if isinstance(value, dict) else getattr(value, name, None)
        if raw is None:
            model_extra = getattr(value, "model_extra", None)
            raw = model_extra.get(name) if isinstance(model_extra, dict) else None
        if raw is None:
            return None
        try:
            return int(raw)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _build_vllm_cache_salt(prompt_cache_key: str) -> str:
        """Derive a protected, stable per-conversation salt for vLLM APC isolation."""

        digest = hmac.new(
            settings.auth_secret_key.encode("utf-8"),
            prompt_cache_key.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    @staticmethod
    def _anthropic_endpoint(base_url: str, path: str) -> str:
        normalized = (base_url or "https://api.anthropic.com").rstrip("/")
        if normalized.endswith("/v1"):
            return f"{normalized}/{path.lstrip('/')}"
        return f"{normalized}/v1/{path.lstrip('/')}"

    @staticmethod
    def _anthropic_headers(api_key: str | None) -> dict[str, str]:
        if not api_key:
            raise ValueError("Anthropic provider requires an API key")
        return {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

    async def _anthropic_json_request(
        self,
        *,
        method: str,
        base_url: str,
        api_key: str | None,
        path: str,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.request(
                    method,
                    self._anthropic_endpoint(base_url, path),
                    headers=self._anthropic_headers(api_key),
                    json=json_body,
                )
                response.raise_for_status()
                payload = response.json()
                return payload if isinstance(payload, dict) else {}
        except httpx.TimeoutException as exc:
            raise RuntimeError("模型服务响应超时，请稍后重试") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError("Anthropic 模型服务调用失败，请检查配置或稍后重试") from exc

    def _build_anthropic_payload(
        self,
        *,
        model_name: str,
        messages: list[dict[str, Any]],
        temperature: float,
        top_p: float,
        max_tokens: int | None,
        stream: bool,
        thinking_enabled: bool,
        thinking_budget: int | None,
        prompt_cache_breakpoint: int | None,
    ) -> dict[str, Any]:
        system_blocks: list[dict[str, Any]] = []
        anthropic_messages: list[dict[str, Any]] = []
        stable_last_index = max(0, min(int(prompt_cache_breakpoint or 0), len(messages))) - 1

        for index, message in enumerate(messages):
            role = str(message.get("role") or "user")
            content = message.get("content")
            blocks = self._anthropic_content_blocks(content)
            if not blocks:
                continue
            if index == stable_last_index:
                blocks[-1] = {**blocks[-1], "cache_control": {"type": "ephemeral"}}
            if role == "system":
                system_blocks.extend(blocks)
                continue
            normalized_role = "assistant" if role == "assistant" else "user"
            if anthropic_messages and anthropic_messages[-1]["role"] == normalized_role:
                anthropic_messages[-1]["content"].extend(blocks)
            else:
                anthropic_messages.append({"role": normalized_role, "content": blocks})

        payload: dict[str, Any] = {
            "model": model_name,
            "messages": anthropic_messages,
            "max_tokens": max(1, int(max_tokens or 4096)),
            "stream": stream,
        }
        if system_blocks:
            payload["system"] = system_blocks
        if thinking_enabled:
            budget = max(1024, int(thinking_budget or 1024))
            payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
            payload["temperature"] = 1
            payload["max_tokens"] = max(payload["max_tokens"], budget + 1)
        else:
            payload["temperature"] = temperature
            payload["top_p"] = top_p
        return payload

    @staticmethod
    def _anthropic_content_blocks(content: Any) -> list[dict[str, Any]]:
        if isinstance(content, str):
            return [{"type": "text", "text": content}]
        if not isinstance(content, list):
            return [{"type": "text", "text": str(content or "")}]
        blocks: list[dict[str, Any]] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") in {"text", "image"}:
                blocks.append(dict(part))
                continue
            if part.get("type") == "image_url":
                url = str((part.get("image_url") or {}).get("url") or "")
                if url.startswith("data:") and ";base64," in url:
                    header, data = url.split(",", 1)
                    blocks.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": header[5:].split(";", 1)[0],
                                "data": data,
                            },
                        }
                    )
        return blocks

    async def _stream_anthropic(
        self,
        **kwargs: Any,
    ) -> AsyncGenerator[ChatStreamEvent, None]:
        payload = self._build_anthropic_payload(
            model_name=kwargs["model_name"],
            messages=kwargs["messages"],
            temperature=kwargs["temperature"],
            top_p=kwargs["top_p"],
            max_tokens=kwargs["max_tokens"],
            stream=True,
            thinking_enabled=kwargs["thinking_enabled"],
            thinking_budget=kwargs["thinking_budget"],
            prompt_cache_breakpoint=kwargs["prompt_cache_breakpoint"],
        )
        usage = {"provider": "anthropic", "input_tokens": 0, "output_tokens": 0,
                 "cache_creation_input_tokens": 0, "cached_input_tokens": 0}
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                async with client.stream(
                    "POST",
                    self._anthropic_endpoint(kwargs["base_url"], "messages"),
                    headers=self._anthropic_headers(kwargs["api_key"]),
                    json=payload,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if not raw or raw == "[DONE]":
                            continue
                        try:
                            event = json.loads(raw)
                        except ValueError:
                            continue
                        event_type = event.get("type")
                        event_usage = (event.get("message") or {}).get("usage") or event.get("usage") or {}
                        if event_usage:
                            usage["input_tokens"] = int(event_usage.get("input_tokens", usage["input_tokens"]) or 0)
                            usage["output_tokens"] = int(event_usage.get("output_tokens", usage["output_tokens"]) or 0)
                            usage["cache_creation_input_tokens"] = int(
                                event_usage.get("cache_creation_input_tokens", usage["cache_creation_input_tokens"]) or 0
                            )
                            usage["cached_input_tokens"] = int(
                                event_usage.get("cache_read_input_tokens", usage["cached_input_tokens"]) or 0
                            )
                        if event_type == "content_block_delta":
                            delta = event.get("delta") or {}
                            text = str(delta.get("text") or delta.get("thinking") or "")
                            if text:
                                yield ChatStreamEvent(
                                    type="reasoning_delta" if delta.get("type") == "thinking_delta" else "answer_delta",
                                    text=text,
                                )
            yield ChatStreamEvent(type="provider_usage", data=usage)
        except httpx.TimeoutException as exc:
            raise RuntimeError("模型服务响应超时，请稍后重试") from exc
        except httpx.HTTPError as exc:
            raise RuntimeError("Anthropic 模型服务调用失败，请检查配置或稍后重试") from exc


def resolve_provider_base_url(
    *,
    provider_type: str,
    configured_base_url: str | None = None,
    configured_api_base_url: str | None = None,
    configured_ollama_base_url: str | None = None,
) -> str:
    # provider_type 决定 base_url 的语义：Ollama 用 ollama_base_url，在线兼容服务用 api_base_url。
    if provider_type == "ollama":
        return configured_ollama_base_url or configured_base_url or settings.ollama_base_url

    return configured_api_base_url or configured_base_url or ""
