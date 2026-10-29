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
        # 正文在前，未命中查询在后；保留后者的范围事实，不能因读取了另一文件而删掉真正的缺失证据。
        answer_sources.sort(key=lambda source: source.source_type == "workspace_file_search"
                            and source.metadata.get("empty_reason") == "no_matching_files")

        intro = (
            "以下是本轮按用户授权调用外部信息工具得到的结果。"
            "引用网页或工具结果时请使用来源编号，并保留来源的时间与证据性质。"
            "用户要求来源链接时，同时给出资料中已有的完整URL，不得仅给编号或编造网址。"
            "documentation_library只是库简介，不支持具体API参数或代码示例；没有正文时应说明尚未查到。"
            "文件搜索未命中只针对该次关键词和检查范围；若后续已经读到正文，应依正文回答，不能仍称文件不存在。"
            "预报、估计或历史记录不能当作当前实况；不足以判断当前状态时明确说明限制。\n\n"
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
