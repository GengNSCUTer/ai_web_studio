from __future__ import annotations

from app.services.tools.schemas import ExternalSource


class ExternalContextAssembler:
    @staticmethod
    def format_sources_for_prompt(sources: list[ExternalSource], *, max_chars: int) -> str | None:
        if not sources:
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
            remaining = max_chars - used_chars
            if remaining <= 0:
                break
            if len(text) > remaining:
                text = text[:remaining].rstrip() + "\n[已按上下文预算截断]"
            parts.append(text)
            used_chars += len(text)
            source.used_in_prompt = True

        if not parts:
            return None

        return (
            "以下是本轮按用户授权调用外部信息工具得到的结果。"
            "涉及实时信息时请优先参考这些来源；引用网页或工具结果时请使用来源编号。\n\n"
            + "\n\n".join(parts)
        )
