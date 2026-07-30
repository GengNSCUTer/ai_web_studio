from __future__ import annotations

from app.services.tools.schemas import ExternalSource


class ExternalContextAssembler:
    MIN_COMPACT_SOURCE_CHARS = 120

    @staticmethod
    def format_sources_for_prompt(sources: list[ExternalSource], *, max_chars: int) -> str | None:
        if not sources:
            return None

        intro = (
            "以下是本轮按用户授权调用外部信息工具得到的结果。"
            "涉及实时信息时请优先参考这些来源；引用网页或工具结果时请使用来源编号。\n\n"
        )
        if max_chars <= 0:
            return None

        parts: list[str] = []
        used_chars = 0
        for index, source in enumerate(sources, start=1):
            previous_label = source.citation_label
            if previous_label and previous_label != f"[T{index}]":
                source.metadata.setdefault("provider_citation_label", previous_label)
            # Provider-local labels restart from 1 on every call. Re-number after
            # aggregation so parallel tools cannot both claim [W1]/[1].
            label = f"[T{index}]"
            source.citation_label = label
            source.used_in_prompt = False
            url_line = f"\nURL: {source.url}" if source.url else ""
            text = (
                f"{label} 类型：{source.source_type}\n"
                f"来源：{source.provider}\n"
                f"标题：{source.title}{url_line}\n"
                f"内容：{source.display_text.strip()}"
            ).strip()
            # A long first web page must not starve later, independent results.
            # Reserve an equal share of the remaining evidence budget for every
            # source still to be formatted. This is prompt compaction only: the
            # complete structured source remains available in trace persistence.
            # `max_chars` has historically budgeted source evidence rather than
            # the fixed instruction header. Preserve that contract for callers
            # with very small test/runtime budgets.
            remaining = max_chars - used_chars
            if remaining <= 0:
                break
            remaining_sources = len(sources) - index + 1
            equal_share = remaining // remaining_sources
            if remaining_sources > 1 and equal_share < ExternalContextAssembler.MIN_COMPACT_SOURCE_CHARS:
                # A few characters of a second source usually contain only the
                # formatter header, not usable evidence. Keep one useful source
                # instead of marking a misleadingly partial second one as used.
                source_budget = remaining
            else:
                source_budget = max(1, equal_share)
            if len(text) > source_budget:
                marker = "\n[结果已按上下文预算压缩]"
                available_text = max(1, source_budget - len(marker))
                text = text[:available_text].rstrip() + marker
            parts.append(text)
            used_chars += len(text)
            source.used_in_prompt = True

        if not parts:
            return None

        return intro + "\n\n".join(parts)
