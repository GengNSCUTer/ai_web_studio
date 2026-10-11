from __future__ import annotations

from app.services.tools.schemas import ExternalSource


class ExternalContextAssembler:
    MIN_COMPACT_SOURCE_CHARS = 120

    @staticmethod
    def format_sources_for_prompt(sources: list[ExternalSource], *, max_chars: int) -> str | None:
        if not sources:
            return None

        # 每次重新组装都重置来源状态，避免小预算下沿用上一轮的注入标记。
        for index, source in enumerate(sources, start=1):
            previous_label = source.citation_label
            if previous_label and previous_label != f"[T{index}]":
                source.metadata.setdefault("provider_citation_label", previous_label)
            source.citation_label = f"[T{index}]"
            source.used_in_prompt = False

        # 库候选用于 Planner 定位文档。已有同 Provider 的正文后，简介不再
        # 竞争最终证据预算；完整候选仍留在 Trace，引用标签也不重新挪用。
        document_providers = {source.provider for source in sources if source.source_type == "documentation"}
        answer_sources = [
            source for source in sources
            if not (source.source_type == "documentation_library" and source.provider in document_providers)
        ]

        intro = (
            "以下是本轮按用户授权调用外部信息工具得到的结果。"
            "涉及实时信息时请优先参考这些来源；引用网页或工具结果时请使用来源编号。\n\n"
        )
        if max_chars <= 0:
            return None

        parts: list[str] = []
        used_chars = 0
        for index, source in enumerate(answer_sources, start=1):
            label = source.citation_label
            url_line = f"\nURL: {source.url}" if source.url else ""
            text = (
                f"{label} 类型：{source.source_type}\n"
                f"来源：{source.provider}\n"
                f"标题：{source.title}{url_line}\n"
                f"内容：{source.display_text.strip()}"
            ).strip()
            # 按剩余来源公平分配证据预算，避免第一篇长文挤掉其他结果。
            # max_chars 沿用现有语义，只预算来源块，不包含固定说明头。
            remaining = max_chars - used_chars
            if remaining <= 0:
                break
            remaining_sources = len(answer_sources) - index + 1
            equal_share = remaining // remaining_sources
            if remaining_sources > 1 and equal_share < ExternalContextAssembler.MIN_COMPACT_SOURCE_CHARS:
                # 极小的份额只有标题而没有正文，优先保留一个可用来源。
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
