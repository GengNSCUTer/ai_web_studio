"""线上引用标签审计；不判断证据是否在语义上支持结论。"""

import re


_CITATION = re.compile(r"(?<!\\)\[(KB|T)\s*(\d{1,9})\]", re.IGNORECASE)
_REFUSAL = re.compile(r"(?:抱歉[，,。\s]*)?(?:现有|当前|提供的|所给)?(?:资料|证据|信息)(?:不足|不充分|缺失|未提供)(?:[，,]\s*(?:无法|不能)(?:据此)?(?:回答|确定|核实)(?:该|这个|上述)?(?:问题|结论|事实)?)?[。.!！]?")


def is_smalltalk_query(query):
    return bool(re.fullmatch(r"\s*(?:你好|您好|谢谢|多谢|再见|hi|hello|thanks|thank you)[！!。.,，\s]*", query or "", re.IGNORECASE))


def without_markdown_code(text):
    """忽略代码示例中的标签，不把字面示例当作正文引用。"""
    lines, fence = [], None
    for line in text.splitlines():
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence) and not line[marker.end():].strip():
                fence = None
            lines.append("")
        elif fence is None:
            lines.append(line)
    return re.sub(r"(`+).*?\1", "", "\n".join(lines), flags=re.DOTALL)


def citation_labels(text):
    return list(dict.fromkeys(f"[{kind.upper()}{int(number)}]" for kind, number in _CITATION.findall(without_markdown_code(text))))


def audit_answer_citations(answer, *, sources, generation_status="done", smalltalk=False, known_labels=()):
    """对每次终态强制审计标签，生成状态与引用质量分开保存。"""
    available = {source["citation_label"] for source in sources
        if source.get("used_in_prompt") is True and (source.get("metadata") or {}).get("citation_scope") == "final_prompt"
        and (source.get("metadata") or {}).get("citation_body_chars", 0) > 0 and source.get("citation_label")}
    known = {source["citation_label"] for source in sources if source.get("citation_label")} | set(known_labels)
    labels = citation_labels(answer)
    unknown = [label for label in labels if label not in known]
    excluded = [label for label in labels if label in known and label not in available]
    prose = without_markdown_code(answer).strip()
    refusal = bool(_REFUSAL.fullmatch(prose))
    missing = bool(available and prose and not labels and not refusal and not smalltalk)
    reasons = (["unknown_citation"] if unknown else []) + (["source_not_in_final_prompt"] if excluded else [])
    if missing:
        reasons.append("missing_citation")
    if known and not available and prose and not refusal and not smalltalk and not labels:
        reasons.append("no_injected_evidence")
    if generation_status != "done":
        status = "incomplete"
        reasons.append("generation_incomplete")
    elif reasons:
        status = "needs_review"
    elif not labels and (not available or not prose or refusal or smalltalk):
        status = "not_applicable"
    else:
        status = "passed"
    return {"version": 1, "status": status, "support_checked": False,
        "cited_labels": labels, "available_labels": sorted(available),
        "unknown_labels": unknown, "excluded_labels": excluded, "missing_citation": missing,
        "reasons": reasons}
