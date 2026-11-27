"""父块只在证据组装时去重；保持原始子块检索排名不变。"""

from dataclasses import replace
import json


def unique_parent_results(results):
    """按用户/知识库/文档/版本/父块归并，同时保留全部命中子块ID。"""
    selected, positions = [], {}
    for result in results:
        metadata = result.metadata or {}
        parent = metadata.get("parent_content")
        if metadata.get("chunk_mode") != "parent_child" or not isinstance(parent, str) or not parent.strip():
            selected.append(result)
            continue
        chunk = result.chunk
        identity = metadata.get("parent_index")
        if identity is None:
            identity = (metadata.get("parent_source_start"), metadata.get("parent_source_end"))
        # 旧数据缺少位置时仅以完整正文判断，不能把未知父块都合并。
        if identity == (None, None):
            identity = parent
        key = (getattr(chunk, "user_id", None), chunk.knowledge_base_id, chunk.document_id,
               getattr(chunk, "index_generation", "legacy"), identity)
        if key in positions:
            position = positions[key]
            kept = selected[position]
            ids = list(dict.fromkeys([*kept.metadata["matched_child_ids"], *metadata.get("matched_child_ids", [chunk.id])]))
            children = list({item["id"]: item for item in [*kept.metadata["matched_children"],
                *metadata.get("matched_children", [_child_location(chunk)])]}.values())
            selected[position] = replace(kept, metadata={**kept.metadata, "matched_child_ids": ids,
                                                          "matched_children": children})
        else:
            positions[key] = len(selected)
            selected.append(replace(result, metadata={**metadata,
                "matched_child_ids": metadata.get("matched_child_ids", [chunk.id]),
                "matched_children": metadata.get("matched_children", [_child_location(chunk)])}))
    return selected


def _child_location(chunk):
    return {"id": str(chunk.id), "content": chunk.content.strip(), "start": getattr(chunk, "source_start", None)}


def _child_span(parent, origin, child):
    text = child["content"]
    if not text:
        return None
    start = child["start"] - origin if isinstance(child["start"], int) else -1
    if start >= 0 and parent.startswith(text, start):
        return start, start + len(text)
    start = parent.find(text)
    if start >= 0 and parent.find(text, start + 1) < 0:
        return start, start + len(text)
    return None


def parent_excerpt(result, limit=None, *, anchors=()):
    """返回正文，以及可在后续预算裁剪时逐段核对的来源范围。"""
    parent = result.metadata.get("parent_content")
    child = _child_location(result.chunk)
    if not isinstance(parent, str) or not parent.strip():
        return child["content"], {}
    raw = parent
    parent = raw.strip()
    origin = (result.metadata.get("parent_source_start") or 0) + len(raw) - len(raw.lstrip())
    children = result.metadata.get("matched_children") or [child]
    located = [(item, span) for item in children if (span := _child_span(parent, origin, item))]
    if limit is None or len(parent) <= limit:
        windows = [(0, len(parent))]
    else:
        sep_cost = len("\n…\n")
        selected = []
        remaining = max(0, limit)
        covered = set()
        while located and len(selected) < 3:
            def priority(entry):
                item, _ = entry
                return len({anchor for anchor in anchors if anchor in item["content"].lower()} - covered)
            item, (start, end) = max(located, key=priority)
            located.remove((item, (start, end)))
            cost = end - start + (sep_cost if selected else 0)
            if cost > remaining or any(start < right and end > left for left, right in selected):
                continue
            selected.append((start, end))
            covered.update(anchor for anchor in anchors if anchor in item["content"].lower())
            remaining -= cost
        windows = sorted(selected)
    if not windows:
        return child["content"], {"evidence_pretruncated": True, "parent_expansion_truncated": True,
            "evidence_original_body_chars": len(parent), "retained_child_ids": [child["id"]],
            "evidence_child_spans": [{"id": child["id"], "source_start": child["start"],
                "source_end": child["start"] + len(child["content"]) if child["start"] is not None else None,
                "body_start": 0, "body_end": len(child["content"])}]}
    spans = []
    range_items = []
    body = ""
    for start, end in windows:
        if body:
            body += "\n…\n"
        position = len(body)
        body += parent[start:end]
        range_items.append(dict(source_start=origin + start, source_end=origin + end, body_start=position,
                                body_end=len(body)))
        for item in children:
            span = _child_span(parent, origin, item)
            if span and start <= span[0] and span[1] <= end:
                spans.append(dict(id=item["id"], source_start=origin + span[0], source_end=origin + span[1],
                    body_start=position + span[0] - start, body_end=position + span[1] - start))
    return body, {"evidence_original_body_chars": len(parent), "parent_expansion_truncated": len(body) < len(parent) or len(windows) > 1,
        "evidence_pretruncated": len(body) < len(parent) or len(windows) > 1,
        "retained_child_ids": list(dict.fromkeys(span["id"] for span in spans)),
        "evidence_child_spans": spans, "retained_ranges": range_items}


def bounded_evidence(result, limit):
    """父块过大时围绕最佳命中子块取窗，而不是只截父块开头。"""
    parent = result.metadata.get("parent_content")
    content = parent if isinstance(parent, str) and parent.strip() else result.chunk.content
    content = content.strip()
    if len(content) <= limit:
        return content
    child = result.chunk.content.strip()
    position = content.find(child) if parent else -1
    start = max(0, position - max(0, limit - len(child)) // 2) if position >= 0 else 0
    start = min(start, max(0, len(content) - limit))
    return content[start:start + limit]


def compact_parent_metadata(chunks, *, apply=False):
    """无损归并旧索引metadata；不更改Child正文、ID、向量或generation。"""
    groups = {}
    for chunk in chunks:
        meta = json.loads(chunk.metadata_json or "{}")
        if meta.get("chunk_mode") != "parent_child" or not isinstance(meta.get("parent_index"), int):
            continue
        key = (chunk.user_id, chunk.knowledge_base_id, chunk.document_id, chunk.index_generation,
               meta["parent_index"], meta.get("parent_source_start"), meta.get("parent_source_end"))
        groups.setdefault(key, []).append((chunk, meta))
    updated, saved, conflicts = 0, 0, 0
    for entries in groups.values():
        texts = {meta["parent_content"] for _, meta in entries if isinstance(meta.get("parent_content"), str) and meta["parent_content"].strip()}
        if len(texts) != 1:
            conflicts += 1
            continue
        anchor = min(entries, key=lambda pair: pair[0].chunk_index)[0]
        content = next(iter(texts))
        for chunk, meta in entries:
            replacement = {**meta, "parent_chunk_index": anchor.chunk_index}
            if chunk is anchor:
                replacement["parent_content"] = content
            else:
                replacement.pop("parent_content", None)
            if replacement == meta:
                continue
            encoded = json.dumps(replacement, ensure_ascii=False)
            saved += len(chunk.metadata_json.encode("utf-8")) - len(encoded.encode("utf-8"))
            updated += 1
            if apply:
                chunk.metadata_json = encoded
    return {"parent_groups": len(groups), "updated_chunks": updated,
            "metadata_bytes_saved": saved, "conflicting_groups_skipped": conflicts}
