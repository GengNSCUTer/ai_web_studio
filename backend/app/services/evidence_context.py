"""结构化证据是 Prompt、引用范围和来源卡片的共同输入。"""

from __future__ import annotations

import re
import json
from dataclasses import dataclass, replace
from typing import Callable

from app.services.tools.schemas import ExternalSource


def complete_excerpt(text: str, limit: int) -> str:
    """节选只停在行、句子或单词边界；不留下半个 Markdown 代码块。"""
    if len(text) <= limit:
        return text.strip()
    if limit <= 0:
        return ""
    if text.lstrip().startswith(("{", "[")):
        try:
            json.loads(text)
        except ValueError:
            pass
        else:
            # JSON整体是一个结构化单元，不能截成语法无效的半份数据。
            return ""
    prefix = text[:limit]
    boundaries = [match.end() for match in re.finditer(r"\n|[。！？!?]|\.(?:\s|$)|\s+", prefix)]
    if not boundaries:
        return ""
    prefix = prefix[:boundaries[-1]].rstrip()
    fence, opened_at, cursor = None, None, 0
    for line in prefix.splitlines(keepends=True):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence, opened_at = token, cursor
            elif token[0] == fence[0] and len(token) >= len(fence) and not line[marker.end():].strip():
                fence, opened_at = None, None
        cursor += len(line)
    if opened_at is not None:
        prefix = prefix[:opened_at].rstrip()
    # 避免行内代码和Markdown链接被截成半个标记。
    ticks = list(re.finditer(r"(?<!\\)(`+)", prefix))
    if len(ticks) % 2:
        prefix = prefix[:ticks[-1].start()].rstrip()
    if prefix.count("[") > prefix.count("]"):
        prefix = prefix[:prefix.rfind("[")].rstrip()
    if prefix.count("](") > prefix.count(")"):
        prefix = prefix[:prefix.rfind("[")].rstrip()
    return prefix


@dataclass(frozen=True)
class EvidenceUnit:
    source: dict
    header: str
    body: str
    original_body_chars: int

    @property
    def label(self) -> str:
        return self.source["citation_label"]

    @property
    def truncated(self) -> bool:
        return bool(self.source["metadata"].get("parent_expansion_truncated")
                    or self.source["metadata"].get("evidence_pretruncated")
                    or len(self.body) < self.original_body_chars)

    def render(self) -> str:
        marker = "\n（此来源仅保留节选，未提供全文。）" if self.truncated else ""
        return f"{self.header}\n内容：{self.body}{marker}"

    def public_source(self) -> dict:
        meta = {**self.source["metadata"], "citation_scope": "final_prompt",
                "citation_body_chars": len(self.body), "citation_body_truncated": self.truncated,
                "retained_body_start": int(self.source["metadata"].get("evidence_body_start") or 0),
                "retained_body_end": int(self.source["metadata"].get("evidence_body_start") or 0) + len(self.body),
                "evidence_original_body_chars": self.original_body_chars}
        return {**self.source, "display_text": self.body, "used_in_prompt": True, "metadata": meta}


@dataclass(frozen=True)
class EvidenceSection:
    layer: str
    intro: str
    units: tuple[EvidenceUnit, ...]

    def render(self) -> str:
        return "\n\n".join(part for part in (self.intro, *(unit.render() for unit in self.units)) if part)

    def fit(self, accepts: Callable[[EvidenceSection], bool]) -> EvidenceSection:
        """逐单元保留或删除，仅在完整单元放不下时尝试显式节选。"""
        if accepts(self):
            return self
        kept: tuple[EvidenceUnit, ...] = ()
        for index, unit in enumerate(self.units):
            candidate = replace(self, units=(*kept, unit))
            if accepts(candidate):
                kept = candidate.units
                continue
            low, high, best = 0, len(unit.body) - 1, ""
            while low <= high:
                middle = (low + high) // 2
                body = complete_excerpt(unit.body, middle)
                candidate = replace(self, units=(*kept, replace(unit, body=body)))
                if accepts(candidate):
                    best = body
                    low = middle + 1
                else:
                    high = middle - 1
            # 过短片段不冒充可用证据；标题永远不能独立取得引用资格。
            if best and len(self.units) - index > 1:
                best = complete_excerpt(best, max(16, len(best) // (len(self.units) - index)))
            if len(best) >= 16:
                kept = (*kept, replace(unit, body=best))
        return replace(self, units=kept)


def build_evidence_section(layer: str, sources: list[ExternalSource], *, intro: str) -> EvidenceSection:
    units, labels = [], set()
    for source in sources:
        public = source.to_public_dict()
        label = public.get("citation_label") or ""
        if not re.fullmatch(r"\[(?:KB|T)\d+\]", label) or label in labels:
            raise ValueError("证据标签必须唯一且由服务端分配")
        labels.add(label)
        body = public["display_text"].strip()
        if not body or not source.used_in_prompt:
            continue
        # 不从正文或标题中解析编号。标题即使包含伪标签，也不能注册新来源。
        header = f"{label} 类型：{public['source_type']}；来源：{public['provider']}\n标题：{public['title']}"
        if public.get("url"):
            header += f"\nURL: {public['url']}"
        meta = public["metadata"]
        if public["source_type"] == "knowledge":
            header += f"\n知识库：{meta.get('knowledge_base_name', 'unknown')}；文件：{meta.get('file_name', 'unknown')}；chunk：{meta.get('chunk_index', '')}"
        units.append(EvidenceUnit(public, header, body,
                                  max(len(body), int(meta.get('evidence_original_body_chars') or 0))))
    return EvidenceSection(layer, intro, tuple(units))


def final_evidence_sources(sections: list[EvidenceSection]) -> dict[str, dict]:
    """最终映射只来自实际送入 Prompt 的单元，不反向扫描已渲染文本。"""
    final = {}
    for section in sections:
        for unit in section.units:
            if unit.label in final:
                raise ValueError("最终证据存在重复标签")
            final[unit.label] = unit.public_source()
    return final
