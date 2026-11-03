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
            ids = list(dict.fromkeys([*kept.metadata["matched_child_ids"], chunk.id]))
            selected[position] = replace(kept, metadata={**kept.metadata, "matched_child_ids": ids})
        else:
            positions[key] = len(selected)
            selected.append(replace(result, metadata={**metadata,
                "matched_child_ids": metadata.get("matched_child_ids", [chunk.id])}))
    return selected


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
