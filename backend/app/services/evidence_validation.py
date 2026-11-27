"""统一核对证据正文与质量，调用方据任务语义决定是否接受节选。"""


def usable_evidence(source, *, complete=False):
    metadata = getattr(source, "metadata", None) or {}
    return (getattr(source, "used_in_prompt", True) and bool(str(getattr(source, "display_text", "") or "").strip())
            and not metadata.get("empty_reason") and metadata.get("quality_status", "valid") == "valid"
            and getattr(source, "source_type", "") not in {"tool_error_feedback", "tool_quality_feedback"}
            and (not complete or not metadata.get("citation_body_truncated")))
