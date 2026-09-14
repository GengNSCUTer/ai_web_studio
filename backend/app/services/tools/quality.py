from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import math
from typing import Any

from app.services.tools.schemas import ExternalSource


QUALITY_STATUSES = {"valid", "uncertain", "invalid"}
LEGACY_QUALITY_STATUS = "unknown"
QUALITY_ACTIONS = {"continue", "retry", "fallback", "replan", "clarify", "block"}
RESULT_SEMANTICS = {"evidence", "empty_answer", "approval_draft"}
SEMANTIC_PROFILES = {
    "web_search",
    "geo_lookup",
    "weather",
    "distance",
    "route",
    "poi_search",
    "file_list",
    "file_search",
    "file_read",
    "artifact_list",
    "artifact_read",
    "approval_draft",
    "file_revision",
}


@dataclass(frozen=True)
class SemanticProfileSpec:
    """一个可复用能力类型的声明约束，而不是 Provider 专用代码。"""

    name: str
    requires_collection: bool


SEMANTIC_PROFILE_SPECS = {
    name: SemanticProfileSpec(
        name=name,
        requires_collection=name
        in {
            "web_search",
            "geo_lookup",
            "weather",
            "distance",
            "route",
            "poi_search",
            "file_list",
            "artifact_list",
        },
    )
    for name in SEMANTIC_PROFILES
}


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
    "semantic_profile",
    "profile_mapping",
    "require_semantic_profile",
    "expected_result_semantics",
}
_NUMERIC_RANGE_KEYS = {"min", "max"}
_PROFILE_MAPPING_KEYS = {
    "evidence_paths",
    "identity_paths",
    "collection_paths",
    "item_evidence_paths",
    "item_identity_paths",
    "min_collection_items",
    "request_matches",
}
_REQUEST_MATCH_KEYS = {"request_path", "result_paths", "normalizer"}
_REQUEST_MATCH_NORMALIZERS = {"text", "text_loose", "coordinate"}
_MAX_PROFILE_PATHS = 16
_MAX_PROFILE_COLLECTIONS = 32
_MAX_PROFILE_ITEMS = 128


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
    if "require_semantic_profile" in contract and not isinstance(contract["require_semantic_profile"], bool):
        raise ValueError("quality_contract.require_semantic_profile must be a boolean.")
    if "expected_result_semantics" in contract:
        expected_semantics = contract["expected_result_semantics"]
        if not isinstance(expected_semantics, str) or expected_semantics.strip().lower() not in RESULT_SEMANTICS:
            raise ValueError(
                "quality_contract.expected_result_semantics must be one of: "
                + ", ".join(sorted(RESULT_SEMANTICS))
                + "."
            )

    if "semantic_profile" in contract:
        profile = contract["semantic_profile"]
        if not isinstance(profile, str) or profile.strip().lower() not in SEMANTIC_PROFILES:
            raise ValueError(
                "quality_contract.semantic_profile must be one of: "
                + ", ".join(sorted(SEMANTIC_PROFILES))
                + "."
            )
        if "profile_mapping" not in contract:
            raise ValueError("quality_contract.semantic_profile requires profile_mapping.")

    if "profile_mapping" in contract:
        if "semantic_profile" not in contract:
            raise ValueError("quality_contract.profile_mapping requires semantic_profile.")
        _validate_profile_mapping(
            contract["profile_mapping"],
            profile=str(contract["semantic_profile"]).strip().lower(),
        )

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

    normalized_contract = dict(contract)
    if "semantic_profile" in normalized_contract:
        # 统一大小写，避免同一 Profile 在 Trace/缓存键中出现多个拼写。
        normalized_contract["semantic_profile"] = str(
            normalized_contract["semantic_profile"]
        ).strip().lower()
    if "expected_result_semantics" in normalized_contract:
        normalized_contract["expected_result_semantics"] = str(
            normalized_contract["expected_result_semantics"]
        ).strip().lower()
    return normalized_contract


def _validate_profile_mapping(value: Any, *, profile: str) -> None:
    """校验声明式 Profile 映射，只允许有限的 JSON Pointer 读取规则。"""

    if not isinstance(value, dict):
        raise ValueError("quality_contract.profile_mapping must be an object.")
    unknown = sorted(set(value) - _PROFILE_MAPPING_KEYS, key=str)
    if unknown:
        raise ValueError(
            "Unsupported quality_contract.profile_mapping fields: "
            + ", ".join(str(item) for item in unknown)
        )

    for field_name in (
        "evidence_paths",
        "identity_paths",
        "collection_paths",
        "item_evidence_paths",
        "item_identity_paths",
    ):
        if field_name in value:
            _validate_profile_pointer_list(
                value[field_name],
                field_name,
                source_paths=field_name not in {"item_evidence_paths", "item_identity_paths"},
            )

    if "evidence_paths" not in value or not value["evidence_paths"]:
        raise ValueError("quality_contract.profile_mapping.evidence_paths must not be empty.")
    if "identity_paths" not in value or not value["identity_paths"]:
        raise ValueError("quality_contract.profile_mapping.identity_paths must not be empty.")
    profile_spec = SEMANTIC_PROFILE_SPECS.get(profile)
    if profile_spec and profile_spec.requires_collection and "collection_paths" not in value:
        raise ValueError(
            f"quality_contract.profile_mapping for {profile} requires collection_paths."
        )
    if "collection_paths" in value and "item_evidence_paths" not in value:
        raise ValueError(
            "quality_contract.profile_mapping.collection_paths requires item_evidence_paths."
        )
    if "item_evidence_paths" in value and "collection_paths" not in value:
        raise ValueError(
            "quality_contract.profile_mapping.item_evidence_paths requires collection_paths."
        )
    if "collection_paths" in value and "item_identity_paths" not in value:
        raise ValueError(
            "quality_contract.profile_mapping.collection_paths requires item_identity_paths."
        )
    if "item_identity_paths" in value and "collection_paths" not in value:
        raise ValueError(
            "quality_contract.profile_mapping.item_identity_paths requires collection_paths."
        )
    if "min_collection_items" in value:
        minimum = value["min_collection_items"]
        if isinstance(minimum, bool) or not isinstance(minimum, int) or not 1 <= minimum <= _MAX_PROFILE_ITEMS:
            raise ValueError(
                "quality_contract.profile_mapping.min_collection_items must be between 1 and 128."
            )
    if "request_matches" in value:
        _validate_request_matches(value["request_matches"])


def _validate_request_matches(value: Any) -> None:
    if not isinstance(value, list) or len(value) > 8:
        raise ValueError(
            "quality_contract.profile_mapping.request_matches must contain 0 to 8 rules."
        )
    for rule in value:
        if not isinstance(rule, dict) or set(rule) - _REQUEST_MATCH_KEYS:
            raise ValueError(
                "quality_contract.profile_mapping.request_matches contains unsupported fields."
            )
        request_path = rule.get("request_path")
        result_paths = rule.get("result_paths")
        normalizer = rule.get("normalizer", "text")
        if not isinstance(request_path, str) or len(request_path) > 256:
            raise ValueError(
                "quality_contract.profile_mapping.request_matches.request_path must be a JSON pointer."
            )
        _validate_request_pointer(request_path)
        _validate_profile_pointer_list(result_paths, "request_matches.result_paths", source_paths=True)
        if not isinstance(normalizer, str) or normalizer not in _REQUEST_MATCH_NORMALIZERS:
            raise ValueError(
                "quality_contract.profile_mapping.request_matches.normalizer is unsupported."
            )


def _validate_profile_pointer_list(value: Any, field_name: str, *, source_paths: bool) -> None:
    if not isinstance(value, list) or not value or len(value) > _MAX_PROFILE_PATHS:
        raise ValueError(f"quality_contract.profile_mapping.{field_name} must contain 1 to 16 paths.")
    for pointer in value:
        if not isinstance(pointer, str) or len(pointer) > 256 or not pointer.startswith("/"):
            raise ValueError(
                f"quality_contract.profile_mapping.{field_name} entries must be JSON-pointer-like paths."
            )
        if "//" in pointer or pointer.endswith("/"):
            raise ValueError(
                f"quality_contract.profile_mapping.{field_name} contains an invalid path."
            )
        # Profile 路径和普通合同路径共用 JSON Pointer 转义规则。否则类似
        # ``~2`` 的拼写会在运行时永远找不到字段，只能靠质量门兜底，而无法
        # 在 Catalog 加载时指出 manifest 配置错误。
        _validate_pointer(pointer, f"quality_contract.profile_mapping.{field_name}")
        if source_paths and pointer != "/sources" and not pointer.startswith("/sources/"):
            raise ValueError(
                f"quality_contract.profile_mapping.{field_name} must start with /sources/."
            )
        if not source_paths and pointer.startswith("/sources/"):
            raise ValueError(
                f"quality_contract.profile_mapping.{field_name} must be relative to one collection item."
            )
        for part in pointer.split("/")[1:]:
            if part == "*" and not source_paths:
                raise ValueError(
                    f"quality_contract.profile_mapping.{field_name} item paths do not support wildcards."
                )


def _validate_pointer_list(value: Any, field_name: str) -> None:
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be an array.")
    for pointer in value:
        _validate_pointer(pointer, field_name)


def _validate_pointer(value: Any, field_name: str) -> None:
    if not isinstance(value, str) or not value.startswith("/"):
        raise ValueError(f"{field_name} entries must be JSON-pointer-like paths.")
    for part in value.split("/")[1:]:
        # JSON Pointer 只允许 ~0 与 ~1 两种转义。错误转义会让规则在运行时
        # 永远解析不到字段，进而把必需质量检查变成静默跳过。
        index = 0
        while index < len(part):
            if part[index] != "~":
                index += 1
                continue
            if index + 1 >= len(part) or part[index + 1] not in {"0", "1"}:
                raise ValueError(f"{field_name} contains an invalid JSON-pointer escape.")
            index += 2


def _validate_request_pointer(value: str) -> None:
    """校验请求上下文路径，禁止会被运行时静默跳过的模糊写法。"""

    field_name = "quality_contract.profile_mapping.request_matches.request_path"
    _validate_pointer(value, field_name)
    if value == "/" or "//" in value or value.endswith("/"):
        raise ValueError(f"{field_name} contains an invalid path.")
    # request_context 是 Schema 校验后的单次参数对象，不支持数组泛化或
    # 通配读取；否则解析失败会被误当成“可选参数不存在”。
    if any("*" in part for part in value.split("/")[1:]):
        raise ValueError(f"{field_name} does not support wildcards.")


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
    """判断成功结果能否供严格依赖、结果绑定或 Durable 成功路径消费。

    ``approval_draft`` 虽然通过了“编辑预览本身是否完整”的质量合同，仍只是
    尚未落盘的待确认草案。它可以在面向用户的受控展示路径中出现，但不能作为
    普通 DAG 依赖、Result Binding 或 Durable Artifact 的成功证据。
    """

    if getattr(result, "status", None) != "success":
        return False
    result_semantics, semantics_valid = _normalize_result_semantics(
        getattr(result, "result_semantics", "evidence")
    )
    if not semantics_valid or result_semantics == "approval_draft":
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
    result_semantics: str = "evidence",
    request_context: dict[str, Any] | None = None,
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
    normalized_semantics, semantics_valid = _normalize_result_semantics(result_semantics)
    if not semantics_valid:
        # 结果语义影响“空响应是否可接受”。未知值绝不能把异常空响应伪装为业务正常结果。
        invalid_reasons.append("unsupported_result_semantics")

    allow_empty = bool(normalized.get("allow_empty", False))
    min_sources = _positive_int(normalized.get("min_sources"), default=1)
    expected_semantics = normalized.get("expected_result_semantics")
    if isinstance(expected_semantics, str) and expected_semantics != normalized_semantics:
        invalid_reasons.append("unexpected_result_semantics")
    if normalized_semantics == "empty_answer" and not allow_empty:
        invalid_reasons.append("empty_answer_not_allowed")
    if not sources:
        if not (normalized_semantics == "empty_answer" and allow_empty and semantics_valid):
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

    profile = normalized.get("semantic_profile")
    profile_mapping = normalized.get("profile_mapping")
    if bool(normalized.get("require_semantic_profile")) and not isinstance(profile, str):
        invalid_reasons.append("semantic_profile_required")
    if isinstance(profile, str) and isinstance(profile_mapping, dict):
        profile_invalid_reasons = _evaluate_semantic_profile(
            envelope=envelope,
            profile=profile.strip().lower(),
            mapping=profile_mapping,
            allow_empty=allow_empty,
            result_semantics=normalized_semantics,
            request_context=request_context or {},
        )
        invalid_reasons.extend(profile_invalid_reasons)

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
            "result_semantics": normalized_semantics,
            "semantic_profile": normalized.get("semantic_profile"),
            "require_semantic_profile": bool(normalized.get("require_semantic_profile")),
            "expected_result_semantics": expected_semantics,
            "request_context_checked": bool(request_context),
        },
    )


class _Missing:
    pass


_MISSING = _Missing()


def _normalize_result_semantics(value: Any) -> tuple[str, bool]:
    """将受限结果语义归一化，未知值按 evidence 失败关闭。"""

    if not isinstance(value, str):
        return "evidence", False
    normalized = value.strip().lower()
    if normalized not in RESULT_SEMANTICS:
        return "evidence", False
    return normalized, True


def _evaluate_semantic_profile(
    *,
    envelope: dict[str, Any],
    profile: str,
    mapping: dict[str, Any],
    allow_empty: bool,
    result_semantics: str,
    request_context: dict[str, Any],
) -> list[str]:
    """按统一映射评估能力 Profile，不执行文本或动态规则。"""

    if profile not in SEMANTIC_PROFILES:
        # Catalog 加载已经会拦截该情况；这里再次失败关闭，保护直接调用方。
        return ["unsupported_semantic_profile"]

    reasons: list[str] = []
    collection_paths = mapping.get("collection_paths") or []
    item_evidence_paths = mapping.get("item_evidence_paths") or []
    item_identity_paths = mapping.get("item_identity_paths") or []
    # ``empty_answer`` 仅能由 Executor 信任的本地 Provider 传入；当合同显式允许时，
    # “没有命中”本身就是业务结果，不应再要求不存在的 evidence / identity 字段。
    empty_business_result = allow_empty and result_semantics == "empty_answer"
    if collection_paths and not empty_business_result:
        collections = _resolve_profile_values(envelope, collection_paths)
        list_collections = [value for value in collections if isinstance(value, list)]
        if not list_collections:
            reasons.append("profile_missing_collection")
        else:
            non_empty_collections = [value for value in list_collections if value]
            if not non_empty_collections:
                # 只有可信 empty_answer 才能把空集合解释为正常业务答案。
                if not (allow_empty and result_semantics == "empty_answer"):
                    reasons.append("profile_empty_collection")
                else:
                    empty_business_result = True
            else:
                minimum = int(mapping.get("min_collection_items") or 1)
                if any(len(items) < minimum for items in non_empty_collections):
                    reasons.append("profile_insufficient_collection_items")
                checked_items = 0
                for items in non_empty_collections:
                    for item in items:
                        checked_items += 1
                        if checked_items > _MAX_PROFILE_ITEMS:
                            reasons.append("profile_collection_too_large")
                            break
                        if not isinstance(item, dict):
                            reasons.append("profile_item_not_object")
                            continue
                        item_evidence_values = _resolve_relative_values(item, item_evidence_paths)
                        if not any(_is_business_value(value) for value in item_evidence_values):
                            reasons.append("profile_item_missing_evidence")
                        item_identity_values = _resolve_relative_values(item, item_identity_paths)
                        if not any(_is_business_value(value) for value in item_identity_values):
                            reasons.append("profile_item_missing_identity")
                    if checked_items > _MAX_PROFILE_ITEMS:
                        break
    # 合法空答案没有 evidence 或 identity 是预期的；其它语义必须满足映射字段。
    if not empty_business_result:
        evidence_values = _resolve_profile_values(envelope, mapping.get("evidence_paths") or [])
        if not any(_is_business_value(value) for value in evidence_values):
            reasons.append("profile_missing_evidence")

        identity_values = _resolve_profile_values(envelope, mapping.get("identity_paths") or [])
        if not any(_is_business_value(value) for value in identity_values):
            reasons.append("profile_missing_identity")
        reasons.extend(_evaluate_request_matches(envelope, mapping, request_context))
    return list(dict.fromkeys(reasons))


def _evaluate_request_matches(
    envelope: dict[str, Any],
    mapping: dict[str, Any],
    request_context: dict[str, Any],
) -> list[str]:
    reasons: list[str] = []
    for index, rule in enumerate(mapping.get("request_matches") or []):
        expected = _resolve_pointer(request_context, str(rule.get("request_path") or ""))
        if expected is _MISSING or _is_empty(expected):
            # 可选请求参数缺失时不强行失败；必填参数已由 Input Schema 在 Executor 前置校验。
            continue
        actual_values = _resolve_profile_values(envelope, rule.get("result_paths") or [])
        normalizer = str(rule.get("normalizer") or "text")
        expected_normalized = _normalize_match_value(expected, normalizer)
        if not expected_normalized or not any(
            _match_values(expected_normalized, _normalize_match_value(value, normalizer), normalizer)
            for value in actual_values
        ):
            reasons.append(f"request_result_mismatch:{index}")
    return reasons


def _normalize_match_value(value: Any, normalizer: str) -> str:
    text = _text_for_quality(value)
    if normalizer == "coordinate":
        parts = [part.strip() for part in text.split(",")]
        if len(parts) == 2:
            try:
                return f"{float(parts[0]):.6f},{float(parts[1]):.6f}"
            except ValueError:
                return ""
        return ""
    normalized = "".join(text.split()).casefold()
    if normalizer == "text_loose":
        for suffix in ("特别行政区", "自治州", "地区", "市", "区", "县", "省"):
            if normalized.endswith(suffix) and len(normalized) > len(suffix):
                normalized = normalized[: -len(suffix)]
                break
    return normalized


def _match_values(expected: str, actual: str, normalizer: str) -> bool:
    if not expected or not actual:
        return False
    if normalizer == "text_loose":
        return expected in actual or actual in expected
    return expected == actual


def _text_for_quality(value: Any) -> str:
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return ""
    return str(value).strip()


def _resolve_profile_values(envelope: dict[str, Any], pointers: list[Any]) -> list[Any]:
    values: list[Any] = []
    for pointer in pointers:
        if not isinstance(pointer, str):
            continue
        values.extend(_resolve_pointer_values(envelope, pointer))
    return values[: _MAX_PROFILE_COLLECTIONS * _MAX_PROFILE_ITEMS]


def _resolve_pointer_values(document: Any, pointer: str) -> list[Any]:
    """解析支持有限 ``*`` 通配的只读 JSON Pointer。"""

    if not isinstance(pointer, str) or not pointer.startswith("/"):
        return []
    parts = pointer.split("/")[1:]
    values: list[Any] = []

    def visit(current: Any, index: int) -> None:
        if len(values) >= _MAX_PROFILE_COLLECTIONS * _MAX_PROFILE_ITEMS:
            return
        if index == len(parts):
            values.append(current)
            return
        part = parts[index].replace("~1", "/").replace("~0", "~")
        if part == "*":
            if isinstance(current, list):
                for item in current[:_MAX_PROFILE_COLLECTIONS]:
                    visit(item, index + 1)
            return
        if isinstance(current, dict) and part in current:
            visit(current[part], index + 1)
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            visit(current[int(part)], index + 1)

    visit(document, 0)
    return values


def _resolve_relative_values(item: dict[str, Any], pointers: list[Any]) -> list[Any]:
    values: list[Any] = []
    for pointer in pointers:
        if not isinstance(pointer, str):
            continue
        value = _resolve_pointer(item, pointer)
        if value is not _MISSING:
            values.append(value)
    return values


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


def _is_business_value(value: Any) -> bool:
    """Profile 的 evidence/identity 必须是非空标量，不能拿容器整体充数。"""

    if isinstance(value, bool) or value is None or isinstance(value, (dict, list, tuple, set)):
        return False
    if isinstance(value, str):
        return not _is_empty(value)
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    return False


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
