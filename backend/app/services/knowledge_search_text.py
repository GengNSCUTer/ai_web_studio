"""检索专用清洗和结构定位；不改证据原文及引用偏移。"""
from __future__ import annotations

import bisect
import json
from pathlib import Path
import re
import unicodedata


def normalize_search_text(text: str) -> str:
    # 只处理可确定的排版噪声，不猜测 Re mote 等词间空格的真实含义。
    text = unicodedata.normalize("NFKC", text).replace("\u00ad", "").replace("\x00", "")
    text = re.sub(r"([A-Za-z]{2,})-\s*\n\s*([a-z]{2,})", r"\1\2", text)
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s*\n\s*(?=[\u4e00-\u9fff])", "", text)
    return re.sub(r"[ \t]+", " ", text).strip()


def structural_markers(markdown: str) -> list[tuple[int, dict]]:
    markers = []
    hierarchy: list[str] = []
    page = None
    for match in re.finditer(r"(?m)^(#{1,6})\s+(.+)$|^([0-9]+(?:\.[0-9]+){0,3})\s+([A-Za-z][^\n]{3,100})$", markdown):
        title = (match.group(2) or (match.group(3) + " " + match.group(4))).strip()
        level = len(match.group(1)) if match.group(1) else min(6, match.group(3).count(".") + 2)
        hierarchy = hierarchy[:level - 1]
        hierarchy.append(title)
        slide = re.match(r"幻灯片\s+(\d+)", title)
        if slide:
            page = int(slide.group(1))
        markers.append((match.start(), dict(section_path=" / ".join(hierarchy)[-400:], slide_number=page)))
    return markers


def metadata_at(markers: list[tuple[int, dict]], offset: int) -> dict:
    i = bisect.bisect_right([m[0] for m in markers], offset) - 1
    return dict(markers[i][1]) if i >= 0 else {}


def retrieval_text(chunk, metadata: dict | None = None) -> str:
    metadata = metadata if metadata is not None else json.loads(chunk.metadata_json or "{}")
    title = Path(metadata.get("file_name") or "").stem
    section = metadata.get("section_path") or ""
    return normalize_search_text(f"{title}\n{section}\n{chunk.content}")
