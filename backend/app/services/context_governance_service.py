from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.tokenizer_service import TokenizerEstimator


@dataclass
class ContextLayerStats:
    system_chars: int = 0
    history_chars: int = 0
    attachment_chars: int = 0
    image_count: int = 0
    file_count: int = 0
    truncated_history_messages: int = 0
    truncated_attachment_chars: int = 0
    total_chars_estimate: int = 0
    total_tokens_estimate: int = 0


@dataclass
class GovernedContext:
    messages: list[dict[str, Any]]
    stats: ContextLayerStats = field(default_factory=ContextLayerStats)
    notices: list[str] = field(default_factory=list)
    summary: str | None = None
    summary_triggered: bool = False
    summary_boundary_message_id: str | None = None


@dataclass(frozen=True)
class ContextBudgetConfig:
    model_context_window: int
    context_mode: str
    max_history_messages: int
    max_total_tokens: int
    max_attachment_tokens: int
    max_image_equiv_tokens: int
    max_summary_tokens: int
    max_total_chars: int
    max_attachment_chars: int
    max_image_equiv_chars: int
    max_summary_chars: int


class ContextBudgetPlanner:
    HARD_MAX_CONTEXT_WINDOW = 262144
    MIN_CONTEXT_WINDOW = 8192
    RESERVED_OUTPUT_TOKENS = 8192
    SUMMARY_REFRESH_MIN_MESSAGES = 6
    SUMMARY_REFRESH_MIN_CHARS = 4000

    MODE_HISTORY_LIMITS = {
        "conservative": 10,
        "balanced": 16,
        "long-context": 24,
    }

    MODE_CHAR_RATIOS = {
        "conservative": 0.32,
        "balanced": 0.45,
        "long-context": 0.6,
    }

    MODE_ATTACHMENT_RATIOS = {
        "conservative": 0.2,
        "balanced": 0.28,
        "long-context": 0.34,
    }

    MODE_SUMMARY_RATIOS = {
        "conservative": 0.1,
        "balanced": 0.12,
        "long-context": 0.14,
    }

    @classmethod
    def build(
        cls,
        *,
        model_context_window: int,
        context_mode: str | None,
    ) -> ContextBudgetConfig:
        normalized_mode = context_mode or "balanced"
        if normalized_mode not in cls.MODE_CHAR_RATIOS:
            normalized_mode = "balanced"

        bounded_window = max(cls.MIN_CONTEXT_WINDOW, min(model_context_window, cls.HARD_MAX_CONTEXT_WINDOW))
        input_token_budget = max(2048, bounded_window - cls.RESERVED_OUTPUT_TOKENS)

        # 当前仍然是字符级治理，这里用保守估算把 token 预算映射到字符预算。
        estimated_chars = input_token_budget * 2
        max_total_tokens = max(4096, int(input_token_budget * cls.MODE_CHAR_RATIOS[normalized_mode]))
        max_total_chars = max(16000, int(estimated_chars * cls.MODE_CHAR_RATIOS[normalized_mode]))
        max_attachment_tokens = max(1000, int(max_total_tokens * cls.MODE_ATTACHMENT_RATIOS[normalized_mode]))
        max_attachment_chars = max(4000, int(max_total_chars * cls.MODE_ATTACHMENT_RATIOS[normalized_mode]))
        max_summary_tokens = max(600, int(max_total_tokens * cls.MODE_SUMMARY_RATIOS[normalized_mode]))
        max_summary_chars = max(2000, int(max_total_chars * cls.MODE_SUMMARY_RATIOS[normalized_mode]))
        max_image_equiv_tokens = max(300, min(1800, int(max_total_tokens * 0.08)))
        max_image_equiv_chars = max(1200, min(6000, int(max_total_chars * 0.08)))
        max_history_messages = cls.MODE_HISTORY_LIMITS[normalized_mode]

        return ContextBudgetConfig(
            model_context_window=bounded_window,
            context_mode=normalized_mode,
            max_history_messages=max_history_messages,
            max_total_tokens=max_total_tokens,
            max_attachment_tokens=max_attachment_tokens,
            max_image_equiv_tokens=max_image_equiv_tokens,
            max_summary_tokens=max_summary_tokens,
            max_total_chars=max_total_chars,
            max_attachment_chars=max_attachment_chars,
            max_image_equiv_chars=max_image_equiv_chars,
            max_summary_chars=max_summary_chars,
        )


class ContextGovernanceService:
    def __init__(self, budget: ContextBudgetConfig | None = None, tokenizer: TokenizerEstimator | None = None):
        self.budget = budget or ContextBudgetPlanner.build(
            model_context_window=128000,
            context_mode="balanced",
        )
        self.tokenizer = tokenizer

    def govern_messages(
        self,
        messages: list[dict[str, Any]],
    ) -> GovernedContext:
        stats = ContextLayerStats()
        notices: list[str] = []

        system_prefix, history_messages = self._split_system_prefix(messages)
        history_slice = history_messages[-self.budget.max_history_messages :]
        stats.truncated_history_messages = max(0, len(history_messages) - len(history_slice))
        selected_messages = [*system_prefix, *history_slice]

        for message in selected_messages:
            estimated = self._estimate_message_chars(message)
            stats.total_chars_estimate += estimated
            if message.get("role") == "system":
                stats.system_chars += estimated
            else:
                stats.history_chars += estimated
        stats.total_tokens_estimate = self._estimate_messages_tokens(selected_messages)

        governed_messages, clipped_chars = self._fit_to_budget(selected_messages)
        stats.total_chars_estimate = self._estimate_messages_chars(governed_messages)
        stats.total_tokens_estimate = self._estimate_messages_tokens(governed_messages)
        governed_history_count = sum(1 for message in governed_messages if message.get("role") != "system")
        stats.truncated_history_messages += max(0, len(history_slice) - governed_history_count)

        summary = None
        summary_triggered = False
        if stats.truncated_history_messages > 0 or clipped_chars > 0:
            summary = self._build_summary(source_messages=messages, clipped_chars=clipped_chars)
            summary_triggered = bool(summary)

        if clipped_chars > 0:
            notices.append(f"上下文已裁剪约 {clipped_chars} 字符，以满足预算限制")
        if stats.truncated_history_messages > 0:
            notices.append(f"历史消息已裁剪 {stats.truncated_history_messages} 条")
        if summary_triggered:
            notices.append("已生成会话摘要，作为压缩记忆保留")

        return GovernedContext(
            messages=governed_messages,
            stats=stats,
            notices=notices,
            summary=summary,
            summary_triggered=summary_triggered,
        )

    @staticmethod
    def _split_system_prefix(messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        prefix: list[dict[str, Any]] = []
        index = 0
        for message in messages:
            if message.get("role") != "system":
                break
            prefix.append(message)
            index += 1
        return prefix, messages[index:]

    async def build_incremental_summary(
        self,
        *,
        existing_summary: str | None,
        summary_boundary_message_id: str | None,
        conversation_messages: list[object],
        summarizer: Any | None = None,
    ) -> tuple[str | None, str | None, dict[str, int]]:
        source_messages = [
            message
            for message in conversation_messages
            if getattr(message, "role", None) in {"user", "assistant"}
        ]
        if not source_messages:
            return existing_summary, summary_boundary_message_id, {
                "summary_refresh_triggered": 0,
                "summary_refresh_source_messages": 0,
                "summary_refresh_source_chars": 0,
                "summary_refresh_source_tokens": 0,
                "summary_refresh_model_used": 0,
                "summary_refresh_fallback_used": 0,
            }

        start_index = 0
        if summary_boundary_message_id:
            for index, message in enumerate(source_messages):
                if getattr(message, "id", None) == summary_boundary_message_id:
                    start_index = index + 1
                    break

        pending_messages = source_messages[start_index:]
        pending_source = pending_messages[:-self.budget.max_history_messages]
        if not pending_source:
            return existing_summary, summary_boundary_message_id, {
                "summary_refresh_triggered": 0,
                "summary_refresh_source_messages": 0,
                "summary_refresh_source_chars": 0,
                "summary_refresh_source_tokens": 0,
                "summary_refresh_model_used": 0,
                "summary_refresh_fallback_used": 0,
            }

        pending_chars = sum(len((getattr(item, "content", None) or "").strip()) for item in pending_source)
        pending_tokens = self._estimate_object_messages_tokens(pending_source)
        should_refresh = (
            existing_summary is None
            or len(pending_source) >= ContextBudgetPlanner.SUMMARY_REFRESH_MIN_MESSAGES
            or pending_chars >= ContextBudgetPlanner.SUMMARY_REFRESH_MIN_CHARS
        )
        if not should_refresh:
            return existing_summary, summary_boundary_message_id, {
                "summary_refresh_triggered": 0,
                "summary_refresh_source_messages": len(pending_source),
                "summary_refresh_source_chars": pending_chars,
                "summary_refresh_source_tokens": pending_tokens,
                "summary_refresh_model_used": 0,
                "summary_refresh_fallback_used": 0,
            }

        summary = None
        model_used = 0
        fallback_used = 0
        if summarizer is not None:
            try:
                summary = await summarizer(
                    existing_summary=existing_summary,
                    source_messages=pending_source,
                    max_summary_chars=self.budget.max_summary_chars,
                )
                model_used = 1 if summary else 0
            except Exception:
                summary = None

        if not summary:
            summary = self._build_structured_summary(
                existing_summary=existing_summary,
                source_messages=pending_source,
            )
            fallback_used = 1 if summary else 0

        boundary_message_id = getattr(pending_source[-1], "id", None)
        return summary, boundary_message_id, {
            "summary_refresh_triggered": 1,
            "summary_refresh_source_messages": len(pending_source),
            "summary_refresh_source_chars": pending_chars,
            "summary_refresh_source_tokens": pending_tokens,
            "summary_refresh_model_used": model_used,
            "summary_refresh_fallback_used": fallback_used,
        }

    def _fit_to_budget(self, messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        current = list(messages)
        clipped_chars = 0

        while self._exceeds_budget(current) and len(current) > 2:
            removable_index = self._find_removable_history_index(current)
            if removable_index is None:
                break

            removed = current.pop(removable_index)
            clipped_chars += self._estimate_message_chars(removed)

        if self._exceeds_budget(current) and current:
            # 最后兜底：截断最后一条用户消息
            last_message = current[-1]
            if last_message.get("role") == "user":
                current[-1], clipped_delta = self._truncate_user_message_to_fit(last_message, current[:-1])
                clipped_chars += clipped_delta

        return current, clipped_chars

    def _exceeds_budget(self, messages: list[dict[str, Any]]) -> bool:
        if self._estimate_messages_chars(messages) > self.budget.max_total_chars:
            return True
        if self._estimate_messages_tokens(messages) > self.budget.max_total_tokens:
            return True
        return False

    @staticmethod
    def _find_removable_history_index(messages: list[dict[str, Any]]) -> int | None:
        for index in range(1, len(messages) - 1):
            if messages[index].get("role") in {"assistant", "user"}:
                return index
        return None

    def _build_file_context(self, file_attachments: list[dict[str, Any]]) -> tuple[str | None, int, int]:
        chunks: list[str] = []
        total_chars = 0
        truncated_chars = 0
        for attachment in file_attachments:
            parsed_text = (attachment.get("parsed_text") or "").strip()
            if not parsed_text:
                continue

            file_name = attachment.get("file_name") or "attachment"
            label = f"[附件文件: {file_name}]"
            limit = self.budget.max_attachment_chars
            sliced = parsed_text[:limit]
            total_chars += len(sliced)
            truncated_chars += max(0, len(parsed_text) - len(sliced))
            chunks.append(f"{label}\n{sliced}")

        if not chunks:
            return None, total_chars, truncated_chars
        return "\n\n".join(chunks).strip(), total_chars, truncated_chars

    @staticmethod
    def _message_text(message: dict[str, Any]) -> str:
        content = message.get("content", "")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(
                part.get("text", "") for part in content if isinstance(part, dict) and "text" in part
            )
        return str(content)

    def _estimate_message_chars(self, message: dict[str, Any]) -> int:
        content = self._message_text(message)
        total = len(content)

        content_value = message.get("content")
        if isinstance(content_value, list):
            for part in content_value:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    total += self.budget.max_image_equiv_chars

        images = message.get("images")
        if isinstance(images, list):
            total += len(images) * self.budget.max_image_equiv_chars

        return total

    def _estimate_messages_chars(self, messages: list[dict[str, Any]]) -> int:
        return sum(self._estimate_message_chars(message) for message in messages)

    def _estimate_message_tokens(self, message: dict[str, Any]) -> int:
        if not self.tokenizer:
            content_chars = self._estimate_message_chars(message)
            return max(1, content_chars // 2)
        return self.tokenizer.estimate_message_tokens(
            message,
            image_equiv_tokens=self.budget.max_image_equiv_tokens,
        )

    def _estimate_messages_tokens(self, messages: list[dict[str, Any]]) -> int:
        if not self.tokenizer:
            return max(1, self._estimate_messages_chars(messages) // 2)
        return self.tokenizer.estimate_messages_tokens(
            messages,
            image_equiv_tokens=self.budget.max_image_equiv_tokens,
        )

    def _estimate_object_messages_tokens(self, messages: list[object]) -> int:
        if not messages:
            return 0
        normalized_messages = [
            {
                "role": getattr(message, "role", "user"),
                "content": getattr(message, "content", "") or "",
            }
            for message in messages
        ]
        return self._estimate_messages_tokens(normalized_messages)

    def _truncate_user_message_to_fit(
        self,
        message: dict[str, Any],
        prefix_messages: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], int]:
        content = message.get("content")
        flat_text = self._message_text(message)
        if not flat_text:
            return message, 0

        low = 0
        high = len(flat_text)
        best = ""
        while low <= high:
            mid = (low + high) // 2
            candidate_message = self._replace_user_text_content(message, content, flat_text[:mid])
            if self._exceeds_budget([*prefix_messages, candidate_message]):
                high = mid - 1
            else:
                best = flat_text[:mid]
                low = mid + 1

        clipped_chars = max(0, len(flat_text) - len(best))
        return self._replace_user_text_content(message, content, best), clipped_chars

    @staticmethod
    def _replace_user_text_content(
        message: dict[str, Any],
        original_content: Any,
        replacement_text: str,
    ) -> dict[str, Any]:
        if isinstance(original_content, str):
            return {**message, "content": replacement_text}
        if isinstance(original_content, list):
            rebuilt_content: list[dict[str, Any]] = []
            text_replaced = False
            for part in original_content:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "image_url":
                    rebuilt_content.append(part)
                    continue
                if part.get("type") == "text" and not text_replaced:
                    rebuilt_content.append({"type": "text", "text": replacement_text})
                    text_replaced = True
            if not text_replaced:
                rebuilt_content.append({"type": "text", "text": replacement_text})
            return {**message, "content": rebuilt_content}
        return {**message, "content": replacement_text}

    def _build_summary(self, *, source_messages: list[dict[str, Any]], clipped_chars: int) -> str | None:
        lines: list[str] = []
        for message in source_messages:
            role = message.get("role")
            if role not in {"user", "assistant"}:
                continue
            text = self._message_text(message).strip()
            if not text:
                continue
            normalized = " ".join(text.split())
            if not normalized:
                continue
            lines.append(f"{role}: {normalized[:320]}")

        if not lines:
            return None

        prefix = ""
        if clipped_chars > 0:
            prefix = f"本轮上下文已裁剪约 {clipped_chars} 字符。"

        summary = prefix + " ".join(lines)
        return summary[: self.budget.max_summary_chars]

    def _build_structured_summary(
        self,
        *,
        existing_summary: str | None,
        source_messages: list[object],
    ) -> str | None:
        user_points: list[str] = []
        assistant_points: list[str] = []
        facts: list[str] = []

        for message in source_messages:
            role = getattr(message, "role", None)
            text = " ".join((getattr(message, "content", None) or "").split()).strip()
            if not text:
                continue

            excerpt = text[:220]
            if role == "user":
                user_points.append(excerpt)
                if any(keyword in text for keyword in ("需要", "希望", "想", "请", "帮我", "要求", "不要")):
                    facts.append(f"用户目标/约束：{excerpt}")
            elif role == "assistant":
                assistant_points.append(excerpt)
                if any(keyword in text for keyword in ("建议", "结论", "可以", "步骤", "方案", "已")):
                    facts.append(f"已有结论/建议：{excerpt}")

        sections: list[str] = []
        if existing_summary:
            sections.append("【已有滚动摘要】")
            sections.append(existing_summary.strip()[: max(1200, self.budget.max_summary_chars // 2)])

        sections.append("【本次新增摘要】")
        if user_points:
            sections.append("用户新增关键信息：")
            sections.extend(f"- {item}" for item in user_points[-6:])
        if assistant_points:
            sections.append("助手新增关键信息：")
            sections.extend(f"- {item}" for item in assistant_points[-6:])
        if facts:
            deduped_facts = list(dict.fromkeys(facts))
            sections.append("沉淀出的事实与约束：")
            sections.extend(f"- {item}" for item in deduped_facts[-8:])

        summary = "\n".join(section for section in sections if section.strip()).strip()
        return summary[: self.budget.max_summary_chars] if summary else None
