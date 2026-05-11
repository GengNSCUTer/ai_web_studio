from __future__ import annotations

import base64
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class PromptBuildResult:
    messages: list[dict[str, Any]]
    diagnostics: dict[str, Any] = field(default_factory=dict)


class ContextPromptBuilder:
    TEMPLATE_VERSION = "context_prompt_v1"

    PROVIDER_TEMPLATES = {
        "ollama": "ollama_chat_v1",
        "openai-compatible": "openai_chat_completions_v1",
    }

    def build_chat_messages(
        self,
        *,
        messages: list[object],
        system_prompt: str | None,
        memory_context: str | None,
        context_summary: str | None,
        summary_boundary_message_id: str | None,
        attachment_context: str | None,
        provider_type: str,
        model_name: str | None = None,
    ) -> PromptBuildResult:
        prompt_messages: list[dict[str, Any]] = []
        layers: list[str] = []

        prompt_messages.append(
            {
                "role": "system",
                "content": self._build_system_instruction(system_prompt),
                "_context_layer": "system",
            }
        )
        layers.append("system")

        if memory_context:
            prompt_messages.append(
                {
                    "role": "system",
                    "content": self._wrap_layer("长期记忆", memory_context),
                    "_context_layer": "long_term_memory",
                }
            )
            layers.append("long_term_memory")

        if context_summary:
            prompt_messages.append(
                {
                    "role": "system",
                    "content": self._wrap_layer(
                        "会话滚动摘要",
                        "以下是本会话较早历史的压缩摘要，请作为长期上下文参考：\n"
                        f"{context_summary}",
                    ),
                    "_context_layer": "conversation_summary",
                }
            )
            layers.append("conversation_summary")

        start_index = self._find_history_start_index(
            messages=messages,
            context_summary=context_summary,
            summary_boundary_message_id=summary_boundary_message_id,
        )

        attachment_context_injected = 0
        image_messages = 0
        history_messages = 0
        recent_messages = [
            message
            for message in messages[start_index:]
            if getattr(message, "role", "") in {"user", "assistant", "system"}
        ]
        last_user_message_id = self._find_last_user_message_id(recent_messages)

        for message in recent_messages:
            role = getattr(message, "role", "")
            if role == "assistant" and getattr(message, "status", None) == "streaming" and not getattr(
                message, "content", None
            ):
                continue

            provider_message = self._build_provider_message(message=message, provider_type=provider_type)
            provider_message["_context_layer"] = "recent_history"
            if (
                attachment_context
                and provider_message.get("role") == "user"
                and getattr(message, "id", None) == last_user_message_id
            ):
                provider_message = self._append_text_to_provider_message(
                    provider_message,
                    self._wrap_layer("当前轮附件片段", f"以下是按当前问题筛选出的附件片段，请结合它回答：\n{attachment_context}"),
                )
                provider_message["_context_layer"] = "recent_history_with_attachment"
                attachment_context_injected = 1

            if self._has_images(provider_message):
                image_messages += 1
            history_messages += 1
            prompt_messages.append(provider_message)

        return PromptBuildResult(
            messages=[self._strip_internal_fields(message) for message in prompt_messages],
            diagnostics={
                "prompt_template_version": self.TEMPLATE_VERSION,
                "provider_template": self.PROVIDER_TEMPLATES.get(provider_type, "generic_chat_v1"),
                "model_family": self._resolve_model_family(model_name),
                "prompt_layers": ",".join(layers + (["recent_history"] if history_messages else [])),
                "prompt_system_layers": len(layers),
                "prompt_history_messages": history_messages,
                "prompt_attachment_context_injected": attachment_context_injected,
                "prompt_image_messages": image_messages,
            },
        )

    def _build_system_instruction(self, system_prompt: str | None) -> str:
        cleaned = (system_prompt or "").strip()
        base = (
            "【AI Web Studio 上下文模板 v1】\n"
            "你会收到按层组织的上下文。请优先遵守系统提示，其次参考长期记忆和会话摘要，"
            "再结合最近消息与当前轮附件回答。若上下文之间冲突，以当前用户消息和最近事实为准。"
        )
        if not cleaned:
            return base
        return f"{base}\n\n【系统提示】\n{cleaned}"

    @staticmethod
    def _wrap_layer(title: str, content: str) -> str:
        return f"【{title}】\n{content.strip()}".strip()

    @staticmethod
    def _find_history_start_index(
        *,
        messages: list[object],
        context_summary: str | None,
        summary_boundary_message_id: str | None,
    ) -> int:
        if not context_summary or not summary_boundary_message_id:
            return 0
        for index, message in enumerate(messages):
            if getattr(message, "id", None) == summary_boundary_message_id:
                return index + 1
        return 0

    @staticmethod
    def _find_last_user_message_id(messages: list[object]) -> str | None:
        for message in reversed(messages):
            if getattr(message, "role", None) == "user":
                return getattr(message, "id", None)
        return None

    def _build_provider_message(self, *, message: object, provider_type: str) -> dict[str, Any]:
        role = getattr(message, "role", "user")
        content = getattr(message, "content", "")
        attachments = getattr(message, "attachments", []) or []

        image_payloads: list[tuple[str, str]] = []
        for attachment in attachments:
            if getattr(attachment, "kind", None) != "image":
                continue

            encoded = self._load_image_base64(getattr(attachment, "storage_path", ""))
            if not encoded:
                continue
            image_payloads.append((encoded, getattr(attachment, "mime_type", None) or "image/jpeg"))

        if role != "user" or not image_payloads:
            return {"role": role, "content": content}

        if provider_type == "ollama":
            return {
                "role": role,
                "content": content,
                "images": [encoded for encoded, _ in image_payloads],
            }

        if provider_type == "openai-compatible":
            content_parts: list[dict[str, Any]] = []
            for encoded, mime_type in image_payloads:
                content_parts.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{encoded}",
                            "detail": "high",
                        },
                    }
                )
            if content.strip():
                content_parts.append({"type": "text", "text": content})
            return {
                "role": role,
                "content": content_parts or [{"type": "text", "text": content}],
            }

        return {"role": role, "content": content}

    @staticmethod
    def _append_text_to_provider_message(message: dict[str, Any], text: str) -> dict[str, Any]:
        content = message.get("content")
        if isinstance(content, list):
            return {
                **message,
                "content": [*content, {"type": "text", "text": text}],
            }
        return {
            **message,
            "content": f"{content or ''}\n\n{text}".strip(),
        }

    @staticmethod
    def _has_images(message: dict[str, Any]) -> bool:
        images = message.get("images")
        if isinstance(images, list) and images:
            return True
        content = message.get("content")
        if not isinstance(content, list):
            return False
        return any(isinstance(part, dict) and part.get("type") == "image_url" for part in content)

    @staticmethod
    def _load_image_base64(storage_path: str) -> str | None:
        try:
            binary = Path(storage_path).read_bytes()
        except OSError:
            return None

        if not binary:
            return None

        return base64.b64encode(binary).decode("utf-8")

    @staticmethod
    def _strip_internal_fields(message: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in message.items() if not key.startswith("_")}

    @staticmethod
    def _resolve_model_family(model_name: str | None) -> str:
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
