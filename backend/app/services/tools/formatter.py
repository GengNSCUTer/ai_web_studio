from __future__ import annotations

from app.services.evidence_context import EvidenceSection, build_evidence_section
from app.services.tools.schemas import ExternalSource


class ExternalContextAssembler:
    INTRO = (
        "以下是本轮授权工具的证据，引用时使用来源编号并保留时间与证据性质。"
        "需要链接时使用资料已有URL，不得编造。documentation_library仅为库简介，不支持具体API用法。"
        "文件未命中仅针对当次关键词和范围；已读到正文时以正文为准。预报、估计和历史记录不能冒充实况。"
    )

    @classmethod
    def build_section(cls, sources: list[ExternalSource], *, max_chars: int,
                      execution_status: str = "") -> EvidenceSection:
        for index, source in enumerate(sources, 1):
            previous = source.citation_label
            if previous and previous != f"[T{index}]":
                source.metadata.setdefault("provider_citation_label", previous)
            source.citation_label = f"[T{index}]"
            source.used_in_prompt = True
        providers = {source.provider for source in sources if source.source_type == "documentation"}
        candidates = [source for source in sources
                      if not (source.source_type == "documentation_library" and source.provider in providers)]
        candidates.sort(key=lambda source: source.source_type == "workspace_file_search"
                        and source.metadata.get("empty_reason") == "no_matching_files")
        intro = "\n\n".join(part for part in (execution_status, cls.INTRO if candidates else "") if part)
        section = build_evidence_section("external_context", candidates, intro=intro)
        section = section.fit(lambda value: len(value.render()) <= max(0, max_chars))
        included = {unit.label for unit in section.units}
        for source in sources:
            source.used_in_prompt = source.citation_label in included
        return section

    @classmethod
    def format_sources_for_prompt(cls, sources: list[ExternalSource], *, max_chars: int) -> str | None:
        section = cls.build_section(sources, max_chars=max_chars)
        return section.render() if section.units else None
