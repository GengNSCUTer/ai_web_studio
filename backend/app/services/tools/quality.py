from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
from typing import Any

from app.services.tools.schemas import ExternalSource


QUALITY_STATUSES = {"valid", "uncertain", "invalid"}
LEGACY_QUALITY_STATUS = "unknown"
QUALITY_ACTIONS = {"continue", "retry", "fallback", "replan", "clarify", "block"}
_QUALITY_CONTRACT_KEYS = {
    "allow_empty",
    "min_sources",
    "required_paths",
    "non_empty_paths",
    "enum_paths",
    "numeric_ranges",
    "confidence_path",
    "min_confidence",
    "freshness_field",
    "max_age_seconds",
}
_NUMERIC_RANGE_KEYS = {"min", "max"}


@dataclass(frozen=True)
class ToolResultQuality:
    """Deterministic business-quality assessment for an executed tool result.

    Execution status answers whether the adapter returned.  This object answers
    whether downstream steps may safely consume the returned evidence.
    """

    status: str
    reasons: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResultQualityDecision:
    """Bounded next action after a tool result passes through the quality gate."""

    action: str
    status: str
    reasons: list[str] = field(default_factory=list)
    retryable: bool = False
    fallback_available: bool = False


def decide_tool_result_action(
    *,
    status: str,
    reasons: list[str] | None = None,
    retryable: bool = False,
    fallback_available: bool = False,
    retry_allowed: bool = False,
    risk_level: str = "low",
    read_only: bool = True,
) -> ToolResultQualityDecision:
    """Choose an explicit, budget-aware action for a quality-gated result.

    The decision is intentionally policy-only. The caller owns the execution
    budget and must perform at most the selected bounded action. In particular,
    an ``uncertain`` result never unlocks a dependent step by itself.
    """

    normalized_status = str(status or "invalid").strip().lower()
    if normalized_status not in QUALITY_STATUSES:
        normalized_status = "invalid"
    normalized_reasons = list(reasons or [])
    normalized_risk = str(risk_level or "low").strip().lower()

    if normalized_status == "valid":
        action = "continue"
    elif normalized_status == "invalid":
        if retry_allowed and retryable:
            action = "retry"
        elif fallback_available and read_only and normalized_risk == "low":
            action = "fallback"
        else:
            action = "replan" if retryable else "block"
    else:  # uncertain
        if fallback_available and read_only and normalized_risk == "low":
            action = "fallback"
        elif normalized_risk == "high" or not read_only:
            action = "clarify"
        else:
            action = "replan"

    return ToolResultQualityDecision(
        action=action,
        status=normalized_status,
        reasons=normalized_reasons,
        retryable=bool(retryable),
        fallback_available=bool(fallback_available),
    )


def validate_quality_contract(contract: dict[str, Any] | None) -> dict[str, Any]:
    """Validate a manifest quality contract before it becomes executable policy.

    A malformed contract must fail while loading the catalog. Silently ignoring a
    misspelled rule would turn a required quality gate into an empty, permissive
    contract and make the failure difficult to diagnose from runtime traces.
    """

    if contract is None:
        return {}
    if not isinstance(contract, dict):
        raise ValueError("Tool quality_contract must be an object.")

    unknown = sorted(set(contract) - _QUALITY_CONTRACT_KEYS, key=str)
    if unknown:
        raise ValueError(f"Unsupported quality contract fields: {', '.join(str(item) for item in unknown)}")

    if "allow_empty" in contract and not isinstance(contract["allow_empty"], bool):
        raise ValueError("quality_contract.allow_empty must be a boolean.")

    if "min_sources" in contract:
        value = contract["min_sources"]
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError("quality_contract.min_sources must be a positive integer.")

    for field_name in ("required_paths", "non_empty_paths"):
        if field_name in contract:
            _validate_pointer_list(contract[field_name], field_name)

    if "enum_paths" in contract:
        enum_paths = contract["enum_paths"]
        if not isinstance(enum_paths, dict):
            raise ValueError("quality_contract.enum_paths must be an object.")
        for path, allowed in enum_paths.items():
            _validate_pointer(path, "quality_contract.enum_paths")
            if not isinstance(allowed, list):
                raise ValueError("quality_contract.enum_paths values must be arrays.")
            for item in allowed:
                if item is not None and not isinstance(item, (str, int, float, bool)):
                    raise ValueError("quality_contract.enum_paths values must contain JSON scalar values.")
                if isinstance(item, float) and not math.isfinite(item):
                    raise ValueError("quality_contract.enum_paths values must contain finite numbers.")

    if "numeric_ranges" in contract:
        numeric_ranges = contract["numeric_ranges"]
        if not isinstance(numeric_ranges, dict):
            raise ValueError("quality_contract.numeric_ranges must be an object.")
        for path, rule in numeric_ranges.items():
            _validate_pointer(path, "quality_contract.numeric_ranges")
            if not isinstance(rule, dict) or not ("min" in rule or "max" in rule):
                raise ValueError("Each quality_contract.numeric_ranges rule needs min or max.")
            unknown_rule_fields = sorted(set(rule) - _NUMERIC_RANGE_KEYS, key=str)
            if unknown_rule_fields:
                raise ValueError(
                    "Unsupported quality_contract.numeric_ranges fields: "
                    + ", ".join(str(item) for item in unknown_rule_fields)
                )
            minimum = _contract_float(rule.get("min")) if "min" in rule else None
            maximum = _contract_float(rule.get("max")) if "max" in rule else None
            if "min" in rule and minimum is None:
                raise ValueError(f"quality_contract.numeric_ranges min is invalid: {path}")
            if "max" in rule and maximum is None:
                raise ValueError(f"quality_contract.numeric_ranges max is invalid: {path}")
            if minimum is not None and maximum is not None and minimum > maximum:
                raise ValueError(f"quality_contract.numeric_ranges min exceeds max: {path}")

    if "confidence_path" in contract:
        _validate_pointer(contract["confidence_path"], "quality_contract.confidence_path")
    if "min_confidence" in contract:
        minimum = _contract_float(contract["min_confidence"])
        if minimum is None or not 0 <= minimum <= 1:
            raise ValueError("quality_contract.min_confidence must be between 0 and 1.")
    if "min_confidence" in contract and "confidence_path" not in contract:
        raise ValueError("quality_contract.min_confidence requires confidence_path.")

    has_freshness_field = "freshness_field" in contract
    has_max_age = "max_age_seconds" in contract
    if has_freshness_field != has_max_age:
        raise ValueError("quality_contract.freshness_field and max_age_seconds must be provided together.")
    if has_freshness_field:
        _validate_pointer(contract["freshness_field"], "quality_contract.freshness_field")
        maximum_age = _contract_float(contract["max_age_seconds"])
        if maximum_age is None or maximum_age < 0:
            raise ValueError("quality_contract.max_age_seconds must be a non-negative number.")

    return dict(contract)


def _validate_pointer_list(value: Any, field_name: str) -> None:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be an array.")
    for pointer in value:
        _validate_pointer(pointer, field_name)


def _validate_pointer(value: Any, field_name: str) -> None:
    if not isinstance(value, str) or not value.startswith("/"):
        raise ValueError(f"{field_name} entries must be JSON-pointer-like paths.")


def quality_status_for_result(result: Any) -> tuple[str, list[str]]:
    """Read quality fields from current and legacy ToolCallResult objects."""

    reasons = list(getattr(result, "quality_reasons", []) or [])
    if not hasattr(result, "quality_status"):
        # Records/test doubles from before the quality fields existed remain
        # compatible, but an explicitly malformed current field is rejected.
        return LEGACY_QUALITY_STATUS, reasons

    raw_status = getattr(result, "quality_status")
    if not isinstance(raw_status, str) or not raw_status.strip():
        reasons.append(f"unsupported_quality_status:{str(raw_status)[:80]}")
        return "invalid", reasons

    status = raw_status.strip().lower()
    if status not in QUALITY_STATUSES and status != LEGACY_QUALITY_STATUS:
        # An executor must never be able to opt out of the quality gate by
        # inventing a new status that the workflow does not understand.
        safe_status = str(raw_status)[:80]
        reasons.append(f"unsupported_quality_status:{safe_status}")
        status = "invalid"
    return status, reasons


def is_usable_tool_result(result: Any) -> bool:
    """Return whether a successful result can be consumed by another step."""

    if getattr(result, "status", None) != "success":
        return False
    status, _ = quality_status_for_result(result)
    # Durable records and test doubles created before quality fields existed are
    # accepted when they contain evidence; current executors always set a status.
    if status == "unknown":
        return bool(getattr(result, "sources", None))
    return status == "valid"


def quality_error_for_result(result: Any) -> str:
    status, reasons = quality_status_for_result(result)
    if status not in {"invalid", "uncertain"}:
        return ""
    suffix = "、".join(reasons[:3])
    return f"工具结果未通过质量门（{status}）" + (f"：{suffix}" if suffix else "。")


def evaluate_tool_result_quality(
    *,
    sources: list[ExternalSource],
    contract: dict[str, Any] | None = None,
) -> ToolResultQuality:
    """Evaluate a bounded result without trusting model-provided content.

    Contracts use JSON-pointer-like paths rooted at ``/sources``.  Values are
    only inspected; they are never executed or interpolated into a tool call.
    An empty contract preserves the existing behavior: a non-empty source list
    is valid and an empty result is invalid.
    """

    normalized = validate_quality_contract(contract)
    envelope = _source_envelope(sources)
    reasons: list[str] = []
    invalid_reasons: list[str] = []
    uncertain_reasons: list[str] = []

    allow_empty = bool(normalized.get("allow_empty", False))
    min_sources = _positive_int(normalized.get("min_sources"), default=1)
    if not sources:
        if not allow_empty:
            invalid_reasons.append("no_sources")
    elif len(sources) < min_sources:
        uncertain_reasons.append("insufficient_sources")

    for path in _string_list(normalized.get("required_paths")):
        value = _resolve_pointer(envelope, path)
        if value is _MISSING or _is_empty(value):
            invalid_reasons.append(f"missing_required:{path}")

    for path in _string_list(normalized.get("non_empty_paths")):
        value = _resolve_pointer(envelope, path)
        if value is _MISSING or _is_empty(value):
            invalid_reasons.append(f"empty_path:{path}")

    enum_paths = normalized.get("enum_paths")
    if isinstance(enum_paths, dict):
        for path, allowed in enum_paths.items():
            if not isinstance(path, str) or not isinstance(allowed, list):
                continue
            value = _resolve_pointer(envelope, path)
            if value is _MISSING:
                invalid_reasons.append(f"missing_enum_path:{path}")
            elif not _enum_contains(allowed, value):
                invalid_reasons.append(f"enum_mismatch:{path}")

    numeric_ranges = normalized.get("numeric_ranges")
    if isinstance(numeric_ranges, dict):
        for path, rule in numeric_ranges.items():
            if not isinstance(path, str) or not isinstance(rule, dict):
                continue
            value = _resolve_pointer(envelope, path)
            if value is _MISSING:
                invalid_reasons.append(f"missing_numeric_path:{path}")
                continue
            try:
                if isinstance(value, bool):
                    raise ValueError
                number = float(value)
            except (TypeError, ValueError):
                invalid_reasons.append(f"not_numeric:{path}")
                continue
            if not math.isfinite(number):
                invalid_reasons.append(f"not_finite:{path}")
                continue
            minimum = _finite_float(rule.get("min"))
            maximum = _finite_float(rule.get("max"))
            if rule.get("min") is not None and minimum is None:
                invalid_reasons.append(f"invalid_min_rule:{path}")
            if rule.get("max") is not None and maximum is None:
                invalid_reasons.append(f"invalid_max_rule:{path}")
            if minimum is not None and number < minimum:
                invalid_reasons.append(f"below_min:{path}")
            if maximum is not None and number > maximum:
                invalid_reasons.append(f"above_max:{path}")

    confidence_path = normalized.get("confidence_path")
    if isinstance(confidence_path, str) and confidence_path:
        value = _resolve_pointer(envelope, confidence_path)
        minimum = _finite_float(normalized.get("min_confidence", 0.0))
        try:
            if isinstance(value, bool):
                raise ValueError
            confidence = float(value)
            if not math.isfinite(confidence) or minimum is None or confidence < minimum:
                uncertain_reasons.append("low_confidence")
        except (TypeError, ValueError):
            uncertain_reasons.append("missing_or_invalid_confidence")

    freshness_field = normalized.get("freshness_field")
    max_age_seconds = normalized.get("max_age_seconds")
    if isinstance(freshness_field, str) and freshness_field and max_age_seconds is not None:
        value = _resolve_pointer(envelope, freshness_field)
        age = _age_seconds(value)
        if age is None:
            uncertain_reasons.append("missing_or_invalid_freshness")
        else:
            maximum_age = _finite_float(max_age_seconds)
            if maximum_age is None:
                uncertain_reasons.append("invalid_max_age")
            elif age > maximum_age:
                uncertain_reasons.append("stale_result")

    reasons.extend(invalid_reasons)
    reasons.extend(uncertain_reasons)
    if invalid_reasons:
        status = "invalid"
    elif uncertain_reasons:
        status = "uncertain"
    else:
        status = "valid"

    return ToolResultQuality(
        status=status,
        reasons=reasons,
        metadata={
            "sources_count": len(sources),
            "min_sources": min_sources,
            "allow_empty": allow_empty,
            "contract_applied": bool(normalized),
        },
    )


class _Missing:
    pass


_MISSING = _Missing()


def _source_envelope(sources: list[ExternalSource]) -> dict[str, Any]:
    return {
        "sources": [
            {
                "source_type": source.source_type,
                "provider": source.provider,
                "title": source.title,
                "display_text": source.display_text,
                "url": source.url,
                "rank": source.rank,
                "score": source.score,
                "metadata": source.metadata or {},
            }
            for source in sources
        ]
    }


def _resolve_pointer(document: Any, pointer: str) -> Any:
    if not pointer.startswith("/"):
        return _MISSING
    current = document
    for raw_part in pointer.split("/")[1:]:
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return _MISSING
    return current


def _is_empty(value: Any) -> bool:
    if value is None or value == [] or value == {}:
        return True
    if not isinstance(value, str):
        return False
    normalized = value.strip().casefold()
    # Adapters sometimes stringify an empty structured payload.  Treating
    # ``[]``/``{}``/"no results" as evidence would incorrectly unlock a
    # dependent tool even though the response is syntactically non-empty.
    return normalized in {
        "",
        "null",
        "none",
        "nil",
        "[]",
        "{}",
        "no results",
        "no result",
        "no matching results",
        "未找到",
        "无结果",
        "暂无结果",
    }


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item.startswith("/")]


def _enum_contains(allowed: list[Any], value: Any) -> bool:
    """Match JSON enum values without Python's bool-is-int coercion."""

    for item in allowed:
        if isinstance(value, bool) or isinstance(item, bool):
            if type(value) is type(item) and value == item:
                return True
            continue
        if value == item:
            return True
    return False


def _positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _contract_float(value: Any) -> float | None:
    """Parse a JSON numeric contract value without accepting booleans."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return _finite_float(value)


def _age_seconds(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if not math.isfinite(timestamp):
            return None
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        return max(0.0, datetime.now(timezone.utc).timestamp() - timestamp)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())
