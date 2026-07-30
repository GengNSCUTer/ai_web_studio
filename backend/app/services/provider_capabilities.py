from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import httpx


PromptCacheMode = Literal[
    "none",
    "provider_automatic",
    "server_automatic",
    "explicit_breakpoint",
]


@dataclass(frozen=True)
class ProviderCapabilities:
    """Request and observability features that are safe for one provider family."""

    family: str
    prompt_cache_mode: PromptCacheMode
    request_cache_key_field: str | None = None
    request_cache_key_in_extra_body: bool = False
    request_stream_usage: bool = False
    cache_usage_support: Literal["none", "provider", "server_config"] = "none"


def resolve_provider_capabilities(*, provider_type: str, base_url: str) -> ProviderCapabilities:
    normalized_type = (provider_type or "").strip().lower()
    host = _normalized_host(base_url)

    if normalized_type == "anthropic":
        return ProviderCapabilities(
            family="anthropic",
            prompt_cache_mode="explicit_breakpoint",
            cache_usage_support="provider",
        )
    if normalized_type == "ollama":
        return ProviderCapabilities(family="ollama", prompt_cache_mode="none")
    if normalized_type == "vllm":
        return ProviderCapabilities(
            family="vllm",
            prompt_cache_mode="server_automatic",
            request_cache_key_field="cache_salt",
            request_cache_key_in_extra_body=True,
            request_stream_usage=True,
            # vLLM only returns cached token details when its server is started
            # with the corresponding prompt-token-details option.
            cache_usage_support="server_config",
        )
    if host == "api.openai.com":
        return ProviderCapabilities(
            family="openai",
            prompt_cache_mode="provider_automatic",
            request_cache_key_field="prompt_cache_key",
            request_stream_usage=True,
            cache_usage_support="provider",
        )
    if host in {"api.siliconflow.cn", "api.siliconflow.com"}:
        return ProviderCapabilities(
            family="siliconflow",
            prompt_cache_mode="provider_automatic",
            # SiliconFlow documents cache hit/miss usage, but not OpenAI's
            # prompt_cache_key request field. Stable prefixes are sufficient.
            cache_usage_support="provider",
        )
    return ProviderCapabilities(
        family="openai_compatible",
        prompt_cache_mode="none",
    )


def _normalized_host(base_url: str) -> str:
    try:
        return (httpx.URL(base_url).host or "").lower()
    except Exception:
        return ""
