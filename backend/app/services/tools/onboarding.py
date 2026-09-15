"""低风险只读 MCP Tool 的接入合同。

本模块只定义并校验接入包，不发送网络请求、不执行映射规则，也不改变 Tool
是否可被 Planner 选择。声明式 Mapper 的执行、fixture 验证和数据库审核状态
会在阶段 2.3B/C/D 分别接入，避免“填写一段 JSON 就自动获得执行权限”。
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from app.services.tools.quality import validate_json_pointer, validate_quality_contract


class ToolOnboardingContractError(ValueError):
    """接入合同不满足受限动态 MCP 安全边界时抛出。"""


ONBOARDING_CONTRACT_VERSION = "tool_onboarding_v1"
ONBOARDING_FIXTURE_FORMAT = "tool_onboarding_fixture_v1"
ONBOARDING_REQUIRED_FIXTURE_CASES = frozenset({"success", "malformed"})
ONBOARDING_OPTIONAL_FIXTURE_CASES = frozenset({"empty", "request_mismatch"})
ONBOARDING_MAPPER_TYPES = frozenset({"object", "collection"})
ONBOARDING_REVIEW_STATUSES = frozenset(
    {"not_configured", "fixture_failed", "pending_review", "approved", "invalidated"}
)

_CONTRACT_KEYS = {
    "version",
    "tool",
    "quality_contract",
    "canonical_mapper",
    "fixture_manifest",
}
_TOOL_IDENTITY_KEYS = {
    "tool_key",
    "provider",
    "category",
    "adapter_type",
    "source_type",
    "risk_level",
    "read_only",
}
_FIXTURE_MANIFEST_KEYS = {"format", "bundle_digest", "case_ids"}
_MAPPER_COMMON_KEYS = {
    "type",
    "display_text_path",
    "title_path",
    "url_path",
    "score_path",
    "canonical_fields",
    "max_chars",
}
_OBJECT_MAPPER_KEYS = _MAPPER_COMMON_KEYS | {"object_path"}
_COLLECTION_MAPPER_KEYS = _MAPPER_COMMON_KEYS | {"collection_path", "max_items"}
_CANONICAL_FIELD_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SHA256 = re.compile(r"^[a-f0-9]{64}$")
_TOOL_KEY = re.compile(r"^[a-z][a-z0-9_.-]{1,219}$")
_RESERVED_CANONICAL_FIELDS = frozenset({"result_semantics", "raw", "metadata"})
_FIXTURE_BUNDLE_KEYS = {"format", "cases"}
_FIXTURE_CASE_KEYS = {"id", "response", "expected", "request_context"}
_FIXTURE_EXPECTED_KEYS = {"quality_status", "source_count"}
_FIXTURE_SOURCE_COUNT_KEYS = {"min", "max"}
_MAX_FIXTURE_BUNDLE_BYTES = 512 * 1024


@dataclass(frozen=True)
class ToolOnboardingContract:
    """经过结构校验的动态 MCP Tool 接入合同。"""

    version: str
    tool: dict[str, Any]
    quality_contract: dict[str, Any]
    canonical_mapper: dict[str, Any]
    fixture_manifest: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """返回稳定、可序列化的合同快照。"""

        return {
            "version": self.version,
            "tool": dict(self.tool),
            "quality_contract": dict(self.quality_contract),
            "canonical_mapper": dict(self.canonical_mapper),
            "fixture_manifest": dict(self.fixture_manifest),
        }

    @property
    def digest(self) -> str:
        """用 canonical JSON 生成版本绑定使用的稳定 SHA-256 摘要。"""

        serialized = json.dumps(
            self.to_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ToolOnboardingFixtureBundle:
    """仅在本地/API 当前请求内使用的脱敏 fixture bundle。

    该对象携带 response 是为了离线执行 Mapper 和质量门；它没有公开序列化方法，
    也绝不能直接写入数据库、Trace 或接入审核记录。
    """

    cases: list[dict[str, Any]]
    digest: str


@dataclass(frozen=True)
class ToolOnboardingFixtureEvaluation:
    """不含 fixture body 的确定性验证报告，可安全返回给审核界面。"""

    valid: bool
    contract_digest: str
    fixture_digest: str
    cases: list[dict[str, Any]]

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "contract_digest": self.contract_digest,
            "fixture_digest": self.fixture_digest,
            "cases": [dict(item) for item in self.cases],
        }


def validate_tool_onboarding_contract(value: Any) -> ToolOnboardingContract:
    """校验一个低风险只读动态 MCP Tool 的完整接入包。

    合同中的 Tool 身份快照会在阶段 2.3D 与数据库 `McpTool` 的当前字段比对。
    当前阶段先把它作为独立且可测试的数据合同，不能仅凭验证成功而启用 Tool。
    """

    if not isinstance(value, dict):
        raise ToolOnboardingContractError("Tool onboarding contract must be an object.")
    _reject_unknown_keys(value, _CONTRACT_KEYS, "Tool onboarding contract")

    version = value.get("version")
    if version != ONBOARDING_CONTRACT_VERSION:
        raise ToolOnboardingContractError(
            f"Tool onboarding contract.version must be {ONBOARDING_CONTRACT_VERSION}."
        )

    tool = _validate_tool_identity(value.get("tool"))
    quality_contract = _validate_onboarding_quality_contract(value.get("quality_contract"))
    canonical_mapper = _validate_canonical_mapper(value.get("canonical_mapper"))
    fixture_manifest = _validate_fixture_manifest(value.get("fixture_manifest"))
    return ToolOnboardingContract(
        version=version,
        tool=tool,
        quality_contract=quality_contract,
        canonical_mapper=canonical_mapper,
        fixture_manifest=fixture_manifest,
    )


def validate_canonical_mapper(value: Any) -> dict[str, Any]:
    """校验并归一化声明式 Mapper，供受控执行器二次防御复用。

    正常路径会先校验完整接入合同；这个公开入口仍保留，以防数据库内容被手工
    修改、历史迁移遗漏或测试直接调用 Mapper 时把不受限的声明带入执行期。
    """

    return _validate_canonical_mapper(value)


def fixture_bundle_digest(value: Any) -> str:
    """计算本地 fixture bundle 的 canonical SHA-256，不向日志输出内容。"""

    try:
        serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ToolOnboardingContractError("Fixture bundle must be JSON serializable.") from exc
    if len(serialized.encode("utf-8")) > _MAX_FIXTURE_BUNDLE_BYTES:
        raise ToolOnboardingContractError("Fixture bundle exceeds the 512 KiB safety limit.")
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def validate_tool_onboarding_fixture_bundle(
    value: Any,
    *,
    contract: ToolOnboardingContract,
) -> ToolOnboardingFixtureBundle:
    """校验仅供本地执行的 fixture bundle，不能把它变成持久化审核数据。"""

    if not isinstance(value, dict):
        raise ToolOnboardingContractError("Tool onboarding fixture bundle must be an object.")
    _reject_unknown_keys(value, _FIXTURE_BUNDLE_KEYS, "Tool onboarding fixture bundle")
    if value.get("format") != ONBOARDING_FIXTURE_FORMAT:
        raise ToolOnboardingContractError(
            f"Tool onboarding fixture bundle.format must be {ONBOARDING_FIXTURE_FORMAT}."
        )
    cases = value.get("cases")
    if not isinstance(cases, list) or not cases:
        raise ToolOnboardingContractError("Tool onboarding fixture bundle.cases must be a non-empty array.")
    if len(cases) > len(ONBOARDING_REQUIRED_FIXTURE_CASES | ONBOARDING_OPTIONAL_FIXTURE_CASES):
        raise ToolOnboardingContractError("Tool onboarding fixture bundle contains too many cases.")

    normalized_cases: list[dict[str, Any]] = []
    seen_case_ids: set[str] = set()
    for case in cases:
        normalized_cases.append(_validate_fixture_case(case, contract=contract, seen_case_ids=seen_case_ids))

    contract_case_ids = set(contract.fixture_manifest["case_ids"])
    if seen_case_ids != contract_case_ids:
        raise ToolOnboardingContractError(
            "Tool onboarding fixture bundle case ids must exactly match fixture_manifest.case_ids."
        )
    digest = fixture_bundle_digest(value)
    if digest != contract.fixture_manifest["bundle_digest"]:
        raise ToolOnboardingContractError(
            "Tool onboarding fixture bundle digest does not match fixture_manifest.bundle_digest."
        )
    return ToolOnboardingFixtureBundle(cases=normalized_cases, digest=digest)


def evaluate_tool_onboarding_fixture_bundle(
    *,
    contract: ToolOnboardingContract,
    bundle: ToolOnboardingFixtureBundle,
) -> ToolOnboardingFixtureEvaluation:
    """离线执行受限 Mapper 与质量合同，并生成不含原始响应的审核报告。"""

    # 局部导入避免 result_mappers -> onboarding 的运行时循环依赖。该调用不发网络，
    # 只处理 fixture 中已经提供的脱敏 JSON。
    from app.services.tools.quality import evaluate_tool_result_quality
    from app.services.tools.result_mappers import map_declared_mcp_result

    reports: list[dict[str, Any]] = []
    all_passed = True
    for case in bundle.cases:
        sources = map_declared_mcp_result(
            canonical_mapper=contract.canonical_mapper,
            provider=contract.tool["provider"],
            category=contract.tool["category"],
            display_name=contract.tool["tool_key"],
            raw=case["response"],
        )
        quality = evaluate_tool_result_quality(
            sources=sources,
            contract=contract.quality_contract,
            request_context=case.get("request_context") or {},
        )
        expected = case["expected"]
        count_rule = expected["source_count"]
        passed = (
            quality.status == expected["quality_status"]
            and count_rule["min"] <= len(sources) <= count_rule["max"]
        )
        all_passed = all_passed and passed
        # 原因来自本地 deterministic quality gate；即便如此也只记录受限 reason，
        # 不把 Provider body、display_text、URL 或 request_context 回显到报告。
        reports.append(
            {
                "id": case["id"],
                "passed": passed,
                "expected_quality_status": expected["quality_status"],
                "actual_quality_status": quality.status,
                "expected_source_count": dict(count_rule),
                "actual_source_count": len(sources),
                "reasons": list(quality.reasons[:12]),
            }
        )
    return ToolOnboardingFixtureEvaluation(
        valid=all_passed,
        contract_digest=contract.digest,
        fixture_digest=bundle.digest,
        cases=reports,
    )


def validate_contract_identity(
    contract: ToolOnboardingContract,
    *,
    current_tool: dict[str, Any],
) -> None:
    """确保合同绑定的是当前这一个动态 MCP Tool，而不是可替换的 call-id。"""

    expected = _validate_tool_identity(current_tool)
    mismatched = [
        field_name
        for field_name in sorted(_TOOL_IDENTITY_KEYS)
        if contract.tool.get(field_name) != expected.get(field_name)
    ]
    if mismatched:
        raise ToolOnboardingContractError(
            "Tool onboarding contract identity does not match the current MCP tool: "
            + ", ".join(mismatched)
        )


def onboarding_config_digest(value: Any) -> str:
    """对当前 Tool/Server 审核输入生成稳定摘要，不持久化完整远端元数据。"""

    try:
        serialized = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ToolOnboardingContractError("MCP onboarding configuration is not JSON serializable.") from exc
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def build_mcp_tool_identity(
    *,
    tool_key: str,
    provider: str,
    category: str,
    risk_level: str,
    read_only: bool,
) -> dict[str, Any]:
    """构造动态 MCP 当前身份快照，供 API、Catalog 复用同一比较规则。"""

    return _validate_tool_identity(
        {
            "tool_key": tool_key,
            "provider": provider,
            "category": category,
            "adapter_type": "mcp_http",
            "source_type": "mcp_server",
            "risk_level": risk_level,
            "read_only": read_only,
        }
    )


def build_mcp_tool_config_digest(
    *,
    tool_key: str,
    raw_name: str,
    display_name: str,
    description: str | None,
    description_override: str | None,
    input_schema_json: str | None,
    output_schema_json: str | None,
    annotations_json: str | None,
    fixed_arguments_json: str | None,
    category: str,
    risk_level: str,
    read_only: bool,
    server_key: str,
    server_url: str,
    server_transport_type: str,
    server_auth_type: str,
    credential_provider: str | None,
    server_project_id: str | None,
) -> str:
    """绑定会影响动态 Tool 行为、候选语义或访问目标的全部配置。

    仅持久化这一摘要，而不将 URL、远端描述、schema 或凭据相关占位内容写入
    审核日志。格式化差异会先被 JSON canonical 化，避免远端只是调整空白就导致
    无意义的重新审核。
    """

    return onboarding_config_digest(
        {
            "tool_key": tool_key,
            "raw_name": raw_name,
            # display_name 会写入 Planner 的候选 Tool 描述，修改它会改变模型
            # 可见的调用语义，不能作为无需复审的纯 UI 字段。
            "display_name": display_name,
            "description": description or "",
            "description_override": description_override or "",
            "input_schema": _canonical_json_snapshot(input_schema_json),
            "output_schema": _canonical_json_snapshot(output_schema_json),
            "annotations": _canonical_json_snapshot(annotations_json),
            "fixed_arguments": _canonical_json_snapshot(fixed_arguments_json),
            "category": category,
            "risk_level": risk_level,
            "read_only": bool(read_only),
            "server_key": server_key,
            "server_url": server_url,
            # transport 决定实际协议语义，project scope 决定 Tool 暴露范围；两者
            # 都不是展示字段，必须进入运行期重算的绑定摘要。
            "server_transport_type": server_transport_type,
            "server_auth_type": server_auth_type,
            "credential_provider": credential_provider or "",
            "server_project_id": server_project_id or "",
        }
    )


def is_mcp_tool_onboarding_approved(
    *,
    contract_json: str | None,
    contract_digest: str | None,
    fixture_digest: str | None,
    config_digest: str | None,
    review_status: str | None,
    current_tool: dict[str, Any],
    current_config_digest: str,
) -> bool:
    """检查持久化审核是否仍与当前 Tool 身份和配置精确绑定。

    此函数用于 Catalog 和执行测试入口。它不信任数据库中的 status 字符串；任何
    JSON 损坏、摘要不一致或当前配置漂移都会返回 ``False``，使 Tool 保持候选集外。
    """

    if review_status != "approved" or not contract_json or not contract_digest or not fixture_digest:
        return False
    if not isinstance(config_digest, str) or config_digest != current_config_digest:
        return False
    try:
        raw_contract = json.loads(contract_json)
        contract = validate_tool_onboarding_contract(raw_contract)
        validate_contract_identity(contract, current_tool=current_tool)
    except (json.JSONDecodeError, ToolOnboardingContractError):
        return False
    return (
        contract.digest == contract_digest
        and contract.fixture_manifest["bundle_digest"] == fixture_digest
    )


def invalidate_mcp_tool_onboarding(tool: Any) -> None:
    """撤销动态 Tool 的接入审核并停止启用，不删除历史合同摘要。"""

    tool.onboarding_review_status = "invalidated" if getattr(tool, "onboarding_contract_json", None) else "not_configured"
    tool.onboarding_reviewed_at = None
    tool.is_enabled = False


def _canonical_json_snapshot(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        # 远端 schema 非法时同样参与摘要，保证“非法值修复/替换”会撤销旧审核；
        # 不把原文返回或持久化到审核报告。
        return {"invalid_json": hashlib.sha256(str(value).encode("utf-8")).hexdigest()}


def _validate_tool_identity(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ToolOnboardingContractError("Tool onboarding contract.tool must be an object.")
    _reject_unknown_keys(value, _TOOL_IDENTITY_KEYS, "Tool onboarding contract.tool")
    required_strings = ("tool_key", "provider", "category", "adapter_type", "source_type", "risk_level")
    for field_name in required_strings:
        field_value = value.get(field_name)
        if not isinstance(field_value, str) or not field_value.strip():
            raise ToolOnboardingContractError(
                f"Tool onboarding contract.tool.{field_name} must be a non-empty string."
            )
    tool_key = str(value["tool_key"]).strip()
    if not _TOOL_KEY.fullmatch(tool_key):
        raise ToolOnboardingContractError("Tool onboarding contract.tool.tool_key is invalid.")
    if str(value["adapter_type"]).strip() != "mcp_http":
        raise ToolOnboardingContractError("Tool onboarding only supports adapter_type=mcp_http.")
    if str(value["source_type"]).strip() != "mcp_server":
        raise ToolOnboardingContractError("Tool onboarding only supports source_type=mcp_server.")
    if str(value["risk_level"]).strip() not in {"low", "medium"}:
        raise ToolOnboardingContractError(
            "Tool onboarding only supports low or medium risk read-only MCP tools."
        )
    if value.get("read_only") is not True:
        raise ToolOnboardingContractError("Tool onboarding requires tool.read_only=true.")
    return {
        "tool_key": tool_key,
        "provider": str(value["provider"]).strip(),
        "category": str(value["category"]).strip(),
        "adapter_type": "mcp_http",
        "source_type": "mcp_server",
        "risk_level": str(value["risk_level"]).strip(),
        "read_only": True,
    }


def _validate_onboarding_quality_contract(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ToolOnboardingContractError("Tool onboarding quality_contract must be an object.")
    try:
        normalized = validate_quality_contract(value)
    except ValueError as exc:
        raise ToolOnboardingContractError(f"Invalid onboarding quality_contract: {exc}") from exc
    if not normalized.get("semantic_profile") or not normalized.get("profile_mapping"):
        raise ToolOnboardingContractError(
            "Tool onboarding quality_contract requires semantic_profile and profile_mapping."
        )
    # 动态远端 MCP 不具备阶段 2.1 的本地 Provider 信任锚；不能将远端空响应
    # 自称为合法 empty_answer。确有特殊业务语义时必须走专用 Adapter 审核。
    if normalized.get("allow_empty"):
        raise ToolOnboardingContractError(
            "Dynamic MCP onboarding cannot set quality_contract.allow_empty=true."
        )
    if normalized.get("expected_result_semantics", "evidence") != "evidence":
        raise ToolOnboardingContractError(
            "Dynamic MCP onboarding only supports expected_result_semantics=evidence."
        )
    return normalized


def _validate_canonical_mapper(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ToolOnboardingContractError("Tool onboarding canonical_mapper must be an object.")
    mapper_type = value.get("type")
    if not isinstance(mapper_type, str) or mapper_type not in ONBOARDING_MAPPER_TYPES:
        raise ToolOnboardingContractError(
            "Tool onboarding canonical_mapper.type must be object or collection."
        )
    allowed_keys = _OBJECT_MAPPER_KEYS if mapper_type == "object" else _COLLECTION_MAPPER_KEYS
    _reject_unknown_keys(value, allowed_keys, "Tool onboarding canonical_mapper")

    root_field = "object_path" if mapper_type == "object" else "collection_path"
    _validate_mapper_pointer(value.get(root_field), root_field)
    _validate_mapper_pointer(value.get("display_text_path"), "display_text_path")
    for field_name in ("title_path", "url_path", "score_path"):
        if field_name in value:
            _validate_mapper_pointer(value[field_name], field_name)

    canonical_fields = value.get("canonical_fields")
    if not isinstance(canonical_fields, dict) or not canonical_fields:
        raise ToolOnboardingContractError(
            "Tool onboarding canonical_mapper.canonical_fields must be a non-empty object."
        )
    if len(canonical_fields) > 16:
        raise ToolOnboardingContractError(
            "Tool onboarding canonical_mapper.canonical_fields supports at most 16 fields."
        )
    normalized_fields: dict[str, str] = {}
    for field_name, pointer in canonical_fields.items():
        if not isinstance(field_name, str) or not _CANONICAL_FIELD_NAME.fullmatch(field_name):
            raise ToolOnboardingContractError("Tool onboarding canonical field name is invalid.")
        if field_name in _RESERVED_CANONICAL_FIELDS:
            raise ToolOnboardingContractError(
                f"Tool onboarding canonical field is reserved: {field_name}."
            )
        _validate_mapper_pointer(pointer, f"canonical_fields.{field_name}")
        normalized_fields[field_name] = pointer

    max_chars = value.get("max_chars", 1600)
    if isinstance(max_chars, bool) or not isinstance(max_chars, int) or not 1 <= max_chars <= 1600:
        raise ToolOnboardingContractError(
            "Tool onboarding canonical_mapper.max_chars must be between 1 and 1600."
        )
    normalized: dict[str, Any] = {
        "type": mapper_type,
        root_field: value[root_field],
        "display_text_path": value["display_text_path"],
        "canonical_fields": normalized_fields,
        "max_chars": max_chars,
    }
    for field_name in ("title_path", "url_path", "score_path"):
        if field_name in value:
            normalized[field_name] = value[field_name]
    if mapper_type == "collection":
        max_items = value.get("max_items", 8)
        if isinstance(max_items, bool) or not isinstance(max_items, int) or not 1 <= max_items <= 16:
            raise ToolOnboardingContractError(
                "Tool onboarding canonical_mapper.max_items must be between 1 and 16."
            )
        normalized["max_items"] = max_items
    return normalized


def _validate_fixture_manifest(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ToolOnboardingContractError("Tool onboarding fixture_manifest must be an object.")
    _reject_unknown_keys(value, _FIXTURE_MANIFEST_KEYS, "Tool onboarding fixture_manifest")
    if value.get("format") != ONBOARDING_FIXTURE_FORMAT:
        raise ToolOnboardingContractError(
            f"Tool onboarding fixture_manifest.format must be {ONBOARDING_FIXTURE_FORMAT}."
        )
    digest = value.get("bundle_digest")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise ToolOnboardingContractError(
            "Tool onboarding fixture_manifest.bundle_digest must be a lowercase SHA-256 digest."
        )
    case_ids = value.get("case_ids")
    if not isinstance(case_ids, list) or not case_ids or not all(isinstance(item, str) for item in case_ids):
        raise ToolOnboardingContractError("Tool onboarding fixture_manifest.case_ids must be a non-empty string list.")
    normalized_case_ids = [item.strip() for item in case_ids]
    if any(not item for item in normalized_case_ids) or len(set(normalized_case_ids)) != len(normalized_case_ids):
        raise ToolOnboardingContractError("Tool onboarding fixture_manifest.case_ids contains duplicate or empty values.")
    allowed_case_ids = ONBOARDING_REQUIRED_FIXTURE_CASES | ONBOARDING_OPTIONAL_FIXTURE_CASES
    unknown_case_ids = sorted(set(normalized_case_ids) - allowed_case_ids)
    if unknown_case_ids:
        raise ToolOnboardingContractError(
            "Tool onboarding fixture_manifest.case_ids contains unsupported values: "
            + ", ".join(unknown_case_ids)
        )
    missing_case_ids = sorted(ONBOARDING_REQUIRED_FIXTURE_CASES - set(normalized_case_ids))
    if missing_case_ids:
        raise ToolOnboardingContractError(
            "Tool onboarding fixture_manifest.case_ids is missing required cases: "
            + ", ".join(missing_case_ids)
        )
    return {
        "format": ONBOARDING_FIXTURE_FORMAT,
        "bundle_digest": digest,
        "case_ids": normalized_case_ids,
    }


def _validate_fixture_case(
    value: Any,
    *,
    contract: ToolOnboardingContract,
    seen_case_ids: set[str],
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ToolOnboardingContractError("Tool onboarding fixture case must be an object.")
    _reject_unknown_keys(value, _FIXTURE_CASE_KEYS, "Tool onboarding fixture case")
    case_id = value.get("id")
    if not isinstance(case_id, str) or case_id.strip() not in (
        ONBOARDING_REQUIRED_FIXTURE_CASES | ONBOARDING_OPTIONAL_FIXTURE_CASES
    ):
        raise ToolOnboardingContractError("Tool onboarding fixture case id is unsupported.")
    case_id = case_id.strip()
    if case_id in seen_case_ids:
        raise ToolOnboardingContractError("Tool onboarding fixture bundle contains duplicate case ids.")
    seen_case_ids.add(case_id)
    response = value.get("response")
    if not isinstance(response, dict):
        raise ToolOnboardingContractError("Tool onboarding fixture case.response must be an MCP response object.")
    expected = _validate_fixture_expected(value.get("expected"), case_id=case_id, contract=contract)
    request_context = _validate_fixture_request_context(value.get("request_context", {}))
    return {
        "id": case_id,
        "response": response,
        "expected": expected,
        "request_context": request_context,
    }


def _validate_fixture_expected(
    value: Any,
    *,
    case_id: str,
    contract: ToolOnboardingContract,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ToolOnboardingContractError("Tool onboarding fixture expected result must be an object.")
    _reject_unknown_keys(value, _FIXTURE_EXPECTED_KEYS, "Tool onboarding fixture expected result")
    quality_status = value.get("quality_status")
    if quality_status not in {"valid", "uncertain", "invalid"}:
        raise ToolOnboardingContractError(
            "Tool onboarding fixture expected.quality_status must be valid, uncertain or invalid."
        )
    # case 名本身有审核语义：正常样本必须能形成可消费 evidence；坏结构和空结果
    # 则必须证明系统会失败关闭。动态远端 MCP 不支持 empty_answer 信任锚。
    fixed_status = {
        "success": "valid",
        "malformed": "invalid",
        "empty": "invalid",
        "request_mismatch": "invalid",
    }.get(case_id)
    if fixed_status and quality_status != fixed_status:
        raise ToolOnboardingContractError(
            f"Tool onboarding fixture case {case_id} must expect quality_status={fixed_status}."
        )
    source_count = value.get("source_count")
    if not isinstance(source_count, dict):
        raise ToolOnboardingContractError("Tool onboarding fixture expected.source_count must be an object.")
    _reject_unknown_keys(source_count, _FIXTURE_SOURCE_COUNT_KEYS, "Tool onboarding fixture expected.source_count")
    minimum = source_count.get("min")
    maximum = source_count.get("max")
    if (
        isinstance(minimum, bool)
        or isinstance(maximum, bool)
        or not isinstance(minimum, int)
        or not isinstance(maximum, int)
        or minimum < 0
        or maximum < minimum
        or maximum > int(contract.canonical_mapper.get("max_items", 1))
    ):
        raise ToolOnboardingContractError("Tool onboarding fixture source_count range is invalid.")
    return {"quality_status": quality_status, "source_count": {"min": minimum, "max": maximum}}


def _validate_fixture_request_context(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ToolOnboardingContractError("Tool onboarding fixture request_context must be an object.")
    if len(value) > 32:
        raise ToolOnboardingContractError("Tool onboarding fixture request_context has too many fields.")
    normalized: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str) or not key or len(key) > 96:
            raise ToolOnboardingContractError("Tool onboarding fixture request_context field name is invalid.")
        if isinstance(item, bool) or item is None or isinstance(item, (dict, list, tuple, set)):
            raise ToolOnboardingContractError(
                "Tool onboarding fixture request_context only supports scalar non-boolean values."
            )
        if isinstance(item, str):
            if len(item) > 2048:
                raise ToolOnboardingContractError("Tool onboarding fixture request_context text is too long.")
            normalized[key] = item
        elif isinstance(item, (int, float)):
            normalized[key] = item
        else:
            raise ToolOnboardingContractError(
                "Tool onboarding fixture request_context only supports scalar non-boolean values."
            )
    return normalized


def _validate_mapper_pointer(value: Any, field_name: str) -> None:
    if not isinstance(value, str) or len(value) > 256:
        raise ToolOnboardingContractError(
            f"Tool onboarding canonical_mapper.{field_name} must be a bounded JSON pointer."
        )
    try:
        validate_json_pointer(
            value,
            f"Tool onboarding canonical_mapper.{field_name}",
            allow_wildcards=False,
            allow_root=False,
        )
    except ValueError as exc:
        raise ToolOnboardingContractError(str(exc)) from exc


def _reject_unknown_keys(value: dict[str, Any], allowed: set[str] | frozenset[str], label: str) -> None:
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise ToolOnboardingContractError(f"{label} contains unsupported fields: {', '.join(unknown)}.")
