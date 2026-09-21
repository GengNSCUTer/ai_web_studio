from __future__ import annotations

import re
from typing import Any

from app.services.tools.quality import validate_json_pointer


EVIDENCE_PROJECTION_PROFILE_VERSION = "evidence_projection_v1"
EVIDENCE_PROJECTION_MODES = frozenset(
    {"none", "facts_only", "bounded_excerpt", "review_required"}
)

_PROFILE_KEYS = {
    "version",
    "mode",
    "allowed_source_types",
    "fact_paths",
    "content_paths",
    "max_sources",
    "max_chars_per_source",
    "max_total_chars",
}
_SOURCE_TYPE = re.compile(r"^[a-z][a-z0-9_.-]{0,63}$")
_FACT_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def default_evidence_projection_profile() -> dict[str, Any]:
    """返回默认关闭的投影配置，避免新 Tool 自动获得正文可见性。"""

    return {
        "version": EVIDENCE_PROJECTION_PROFILE_VERSION,
        "mode": "none",
    }


def validate_evidence_projection_profile(
    value: Any,
    *,
    allow_bounded_excerpt: bool = True,
) -> dict[str, Any]:
    """校验并归一化一个声明式 Planner evidence 投影配置。

    Profile 只描述“已规范化的 Tool evidence 中哪些内容可被下一轮 Planner
    看到”，不负责授予 Tool 权限，也不替代 Onboarding、质量门或 Executor。
    未配置时固定归一化为 ``none``，保证新 Tool 失败关闭。
    """

    if value is None or value == {}:
        return default_evidence_projection_profile()
    if not isinstance(value, dict):
        raise ValueError("evidence_projection must be an object.")

    unknown = sorted(set(value) - _PROFILE_KEYS)
    if unknown:
        raise ValueError(
            "Unsupported evidence_projection fields: " + ", ".join(unknown)
        )
    if value.get("version") != EVIDENCE_PROJECTION_PROFILE_VERSION:
        raise ValueError(
            "evidence_projection.version must be "
            f"{EVIDENCE_PROJECTION_PROFILE_VERSION}."
        )

    mode = value.get("mode")
    if mode not in EVIDENCE_PROJECTION_MODES:
        raise ValueError(
            "evidence_projection.mode must be none, facts_only, "
            "bounded_excerpt or review_required."
        )
    if mode == "bounded_excerpt" and not allow_bounded_excerpt:
        raise ValueError(
            "Dynamic MCP onboarding cannot enable bounded_excerpt in stage 4.3A."
        )

    if mode in {"none", "review_required"}:
        extra = sorted(
            set(value)
            - {
                "version",
                "mode",
            }
        )
        if extra:
            raise ValueError(
                f"evidence_projection mode {mode} does not accept excerpt fields: "
                + ", ".join(extra)
            )
        return {
            "version": EVIDENCE_PROJECTION_PROFILE_VERSION,
            "mode": mode,
        }

    source_types = _validate_source_types(value.get("allowed_source_types"))
    fact_paths = _validate_fact_paths(value.get("fact_paths"))
    if mode == "facts_only":
        if not fact_paths:
            raise ValueError(
                "evidence_projection mode facts_only requires fact_paths."
            )
        unexpected = sorted(
            set(value)
            - {"version", "mode", "allowed_source_types", "fact_paths"}
        )
        if unexpected:
            raise ValueError(
                "evidence_projection mode facts_only does not accept excerpt fields: "
                + ", ".join(unexpected)
            )
        return {
            "version": EVIDENCE_PROJECTION_PROFILE_VERSION,
            "mode": "facts_only",
            "allowed_source_types": source_types,
            "fact_paths": fact_paths,
        }

    content_paths = _validate_content_paths(value.get("content_paths"))
    # Profile 负责给每个 Tool 一份有限预算；默认值应足够支持多来源事实，
    # 但仍保持硬上限。具体 Tool 可以按业务需要进一步收紧，不能通过 Profile
    # 超出全局校验上限。
    max_sources = _bounded_int(
        value.get("max_sources", 4),
        field_name="max_sources",
        minimum=1,
        maximum=8,
    )
    max_chars_per_source = _bounded_int(
        value.get("max_chars_per_source", 720),
        field_name="max_chars_per_source",
        minimum=80,
        maximum=1600,
    )
    max_total_chars = _bounded_int(
        value.get("max_total_chars", min(2400, max_sources * max_chars_per_source)),
        field_name="max_total_chars",
        minimum=max_chars_per_source,
        maximum=6400,
    )
    return {
        "version": EVIDENCE_PROJECTION_PROFILE_VERSION,
        "mode": "bounded_excerpt",
        "allowed_source_types": source_types,
        "fact_paths": fact_paths,
        "content_paths": content_paths,
        "max_sources": max_sources,
        "max_chars_per_source": max_chars_per_source,
        "max_total_chars": max_total_chars,
    }


def _validate_source_types(value: Any) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= 8:
        raise ValueError(
            "evidence_projection.allowed_source_types must contain 1 to 8 values."
        )
    normalized: list[str] = []
    for item in value:
        candidate = str(item or "").strip().lower()
        if not _SOURCE_TYPE.fullmatch(candidate):
            raise ValueError(
                "evidence_projection.allowed_source_types contains an invalid value."
            )
        if candidate not in normalized:
            normalized.append(candidate)
    return normalized


def _validate_content_paths(value: Any) -> list[str]:
    if not isinstance(value, list) or not 1 <= len(value) <= 8:
        raise ValueError(
            "evidence_projection.content_paths must contain 1 to 8 JSON pointers."
        )
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(
                "evidence_projection.content_paths entries must be strings."
            )
        validate_json_pointer(item, "evidence_projection.content_paths")
        # Planner 摘录只能读取 Mapper 生成并经 Executor 脱敏的 canonical raw
        # 标量，不能借 Profile 重新开放 title、URL、display_text 或任意 metadata。
        if not item.startswith("/metadata/raw/") or "*" in item:
            raise ValueError(
                "evidence_projection.content_paths must target scalar "
                "/metadata/raw/... fields without wildcards."
            )
        if item not in normalized:
            normalized.append(item)
    return normalized


def _validate_fact_paths(value: Any) -> dict[str, str]:
    if value is None or value == {}:
        return {}
    if not isinstance(value, dict) or not 1 <= len(value) <= 16:
        raise ValueError(
            "evidence_projection.fact_paths must contain 1 to 16 named JSON pointers."
        )
    normalized: dict[str, str] = {}
    for fact_name, pointer in value.items():
        if not isinstance(fact_name, str) or not _FACT_NAME.fullmatch(fact_name):
            raise ValueError("evidence_projection.fact_paths contains an invalid fact name.")
        if fact_name in {"call_id", "tool_key"}:
            raise ValueError(
                "evidence_projection.fact_paths cannot override platform identity fields."
            )
        if not isinstance(pointer, str):
            raise ValueError("evidence_projection.fact_paths entries must be strings.")
        validate_json_pointer(pointer, "evidence_projection.fact_paths")
        if not pointer.startswith("/metadata/") or "*" in pointer:
            raise ValueError(
                "evidence_projection.fact_paths must target scalar /metadata/... "
                "fields without wildcards."
            )
        normalized[fact_name] = pointer
    return normalized


def _bounded_int(
    value: Any,
    *,
    field_name: str,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"evidence_projection.{field_name} must be an integer.")
    if not minimum <= value <= maximum:
        raise ValueError(
            f"evidence_projection.{field_name} must be between {minimum} and {maximum}."
        )
    return value
