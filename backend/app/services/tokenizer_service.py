from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import tiktoken


@dataclass(frozen=True)
class TokenizerEstimate:
    encoding_name: str
    model_family: str


class TokenizerEstimator:
    DEFAULT_ENCODING = "cl100k_base"
    MODEL_FAMILY_ENCODINGS = {
        "openai": "o200k_base",
        "qwen": "cl100k_base",
        "deepseek": "cl100k_base",
        "claude": "cl100k_base",
        "gemini": "cl100k_base",
        "generic": "cl100k_base",
    }

    def __init__(self, *, model_name: str | None = None):
        self.model_family = self._resolve_model_family(model_name)
        encoding_name = self.MODEL_FAMILY_ENCODINGS.get(self.model_family, self.DEFAULT_ENCODING)
        self.encoding_name = encoding_name
        self.encoding = tiktoken.get_encoding(encoding_name)

    @property
    def estimate(self) -> TokenizerEstimate:
        return TokenizerEstimate(
            encoding_name=self.encoding_name,
            model_family=self.model_family,
        )

    def estimate_text_tokens(self, text: str | None) -> int:
        normalized = text or ""
        if not normalized:
            return 0
        return len(self.encoding.encode(normalized))

    def estimate_message_tokens(self, message: dict[str, Any], *, image_equiv_tokens: int) -> int:
        base_tokens = 4
        content = message.get("content")
        total = base_tokens + self.estimate_content_tokens(content)

        if isinstance(message.get("images"), list):
            total += len(message["images"]) * image_equiv_tokens
        return total

    def estimate_content_tokens(self, content: Any) -> int:
        if isinstance(content, str):
            return self.estimate_text_tokens(content)
        if isinstance(content, list):
            total = 0
            for part in content:
                if not isinstance(part, dict):
                    total += self.estimate_text_tokens(str(part))
                    continue
                if part.get("type") == "text":
                    total += self.estimate_text_tokens(part.get("text"))
                elif part.get("type") == "image_url":
                    total += 0
                else:
                    total += self.estimate_text_tokens(str(part))
            return total
        if content is None:
            return 0
        return self.estimate_text_tokens(str(content))

    def estimate_messages_tokens(self, messages: list[dict[str, Any]], *, image_equiv_tokens: int) -> int:
        return sum(self.estimate_message_tokens(message, image_equiv_tokens=image_equiv_tokens) for message in messages) + 2

    @classmethod
    def _resolve_model_family(cls, model_name: str | None) -> str:
        normalized = (model_name or "").lower()
        if "qwen" in normalized:
            return "qwen"
        if "deepseek" in normalized:
            return "deepseek"
        if "gemini" in normalized:
            return "gemini"
        if "claude" in normalized:
            return "claude"
        if "gpt" in normalized or "openai" in normalized:
            return "openai"
        return "generic"
