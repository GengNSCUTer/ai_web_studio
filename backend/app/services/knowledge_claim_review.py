"""离线核验答案结论与实际使用的引用证据。"""

import re

from app.services.citation_audit_service import citation_labels, without_markdown_code


_MAX_CLAIMS = 24
_VERDICTS = {"supported", "contradicted", "insufficient"}
_BOUNDARY = re.compile(r"(?<=[。！？!?])\s*|(?<=\.)\s+(?=[A-Za-z\u4e00-\u9fff])|\n+")
_LEADING_CITATIONS = re.compile(r"^((?:\[(?:KB|T)\s*\d+\][ \t]*)+)", re.I)


def extract_claims(answer, source_map):
    sentences = []
    for part in _BOUNDARY.split(without_markdown_code(answer)):
        clean = part.strip()
        labels = _LEADING_CITATIONS.match(clean)
        if labels and sentences:
            sentences[-1] += " " + labels[1]
            clean = clean[labels.end():].strip()
        if clean and re.search(r"[A-Za-z\u4e00-\u9fff0-9]", clean) and not clean.startswith("#") and not clean.endswith(("?", "？")):
            sentences.append(clean)
    claims = []
    for index, sentence in enumerate(sentences[:_MAX_CLAIMS], 1):
        labels = citation_labels(sentence)
        valid = [label for label in labels if label in source_map and source_map[label].strip()]
        claims.append(dict(id=index, text=sentence[:1000], labels=labels, available_labels=valid,
                           verdict="awaiting_judge" if valid else "missing_citation"))
    return claims, max(0, len(sentences) - len(claims))


def review_claims(answer, sources, judge):
    visible = {f"[{entry['label']}]": entry["content"] for entry in sources}
    claims, omitted = extract_claims(answer, visible)
    pending = [claim for claim in claims if claim["available_labels"]]
    decisions = {}
    if pending:
        try:
            payload = judge(pending, {label: visible[label] for claim in pending for label in claim["available_labels"]})
            for row in payload.get("verdicts", []) if isinstance(payload, dict) else []:
                if isinstance(row, dict) and type(row.get("id")) is int and row.get("verdict") in _VERDICTS:
                    decisions.setdefault(row["id"], row["verdict"])
        except Exception:
            pass
    for claim in pending:
        claim["verdict"] = decisions.get(claim["id"], "judge_unavailable")
    count = len(claims)
    checked = sum(claim["verdict"] != "judge_unavailable" for claim in claims)
    supported = sum(claim["verdict"] == "supported" for claim in claims)
    return {"claims": claims, "omitted_claims": omitted, "checked_claims": checked,
        "citation_coverage": len(pending) / count if count else None,
        "support_rate": supported / checked if checked else None,
        "unsupported_claim_rate": (checked - supported) / checked if checked else None}
