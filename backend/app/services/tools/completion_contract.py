from __future__ import annotations

"""Skill 级任务完成条件。

Planner 可以自由选择候选工具，但不能用一个看似合理的短路径跳过 Skill
明确要求的证据步骤。合同只声明触发词、前置工具和必须出现的工具；具体
补充调用仍要通过既有的 Catalog、Schema、权限与工作流校验。
"""

from typing import Any
from uuid import uuid4
import re

from app.services.skill_catalog import SkillExecutionContext
from app.services.tools.catalog import ToolCatalog
from app.services.tools.schemas import PlannedToolCall, ToolPlan


def contract_applies(*, query: str, contract: dict[str, Any] | None) -> bool:
    """判断当前问题是否触发声明式完成合同。"""

    if not isinstance(contract, dict):
        return False
    normalized = str(query or "").strip().lower()
    return bool(
        normalized
        and any(
            str(pattern).strip().lower() in normalized
            for pattern in (contract.get("intent_patterns") or [])
            if str(pattern).strip()
        )
    )


def required_tool_keys(contract: dict[str, Any] | None) -> tuple[str, ...]:
    """返回合同要求的工具，不负责授予额外权限。"""

    if not isinstance(contract, dict):
        return ()
    return tuple(
        dict.fromkeys(
            str(item).strip()
            for item in (contract.get("required_tool_keys") or [])
            if str(item).strip()
        )
    )


def _explicit_replacement(query: str) -> tuple[str, str] | None:
    """仅识别用户直接给出的带引号的精确替换，不猜测自然语言编辑意图。"""

    normalized = str(query or "").strip()
    patterns = (
        r"(?:把|将)\s*[\"“'](.+?)[\"”']\s*(?:替换为|改为|修改为|更新为)\s*[\"“'](.+?)[\"”']",
        r"(?:replace)\s+[\"'](.+?)[\"']\s+(?:with)\s+[\"'](.+?)[\"']",
    )
    for pattern in patterns:
        match = re.search(pattern, normalized, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        old_string, new_string = (item.strip() for item in match.groups())
        if old_string and old_string != new_string and len(old_string) <= 12_000 and len(new_string) <= 12_000:
            return old_string, new_string
    return None


def _effective_required_tool_keys(*, query: str, contract: dict[str, Any] | None) -> tuple[str, ...]:
    """根据受审核策略缩小必要步骤，避免无明确替换文本时自动进入写操作。"""

    required = list(required_tool_keys(contract))
    if (
        isinstance(contract, dict)
        and contract.get("require_edit_for_exact_replacement")
        and _explicit_replacement(query) is None
    ):
        required = [key for key in required if key != "workspace.files.apply_edit"]
    return tuple(required)


def needs_followup(
    *,
    query: str,
    observations: list[dict[str, Any]],
    contract: dict[str, Any] | None,
) -> bool:
    """当前已有前置证据但缺少必需证据时，要求有限重规划。"""

    if not contract_applies(query=query, contract=contract):
        return False
    observed_tools = {
        str(item.get("metadata", {}).get("tool_key"))
        for item in observations
        if isinstance(item, dict) and isinstance(item.get("metadata"), dict)
    }
    required = set(_effective_required_tool_keys(query=query, contract=contract))
    if not required or required.issubset(observed_tools):
        return False
    prerequisites = {
        str(item).strip()
        for item in (contract or {}).get("prerequisite_tool_keys") or []
        if str(item).strip()
    }
    return bool(prerequisites.intersection(observed_tools))


def augment_plan(
    *,
    plan: ToolPlan,
    query: str,
    observations: list[dict[str, Any]],
    skill_context: SkillExecutionContext | None,
    catalog: ToolCatalog,
) -> bool:
    """使用已获得的 opaque file_id 补充缺失的只读文件读取调用。

    只处理当前已声明的 `workspace.files.read` 合同：文件 ID 必须来自执行器
    生成的受限 observation，且 Tool 必须仍在显式 Skill allowlist 内。
    """

    contract = skill_context.completion_contract if skill_context else None
    if not contract_applies(query=query, contract=contract):
        return False
    existing = {call.tool_key for call in plan.calls}
    missing = [key for key in _effective_required_tool_keys(query=query, contract=contract) if key not in existing]
    if not missing:
        return False
    allowed = set(skill_context.allowed_tool_keys if skill_context else ())
    changed = False
    for tool_key in missing:
        if tool_key not in allowed:
            continue
        definition = catalog.get_or_none(tool_key)
        if definition is None:
            continue
        strategy = str((contract or {}).get("completion_strategy") or "workspace_file_read")
        if tool_key == "workspace.files.apply_edit":
            if strategy != "workspace_file_read_then_apply_exact_replacement":
                continue
            replacement = _explicit_replacement(query)
            if replacement is None:
                continue
            candidate = next(
                (
                    item
                    for item in observations
                    if isinstance(item, dict)
                    and item.get("source_type") == "workspace_file_read"
                    and isinstance(item.get("metadata"), dict)
                    and str(item["metadata"].get("file_id") or "").strip()
                ),
                None,
            )
            if candidate is None:
                continue
            metadata = candidate["metadata"]
            old_string, new_string = replacement
            arguments = {
                "file_id": str(metadata["file_id"]).strip(),
                "old_string": old_string,
                "new_string": new_string,
            }
            if metadata.get("revision_id"):
                arguments["expected_revision_id"] = str(metadata["revision_id"])
            plan.calls.append(
                PlannedToolCall(
                    call_id=f"contract_edit_{uuid4().hex[:12]}",
                    tool_key=tool_key,
                    provider=definition.provider,
                    category=definition.category,
                    display_name=definition.display_name,
                    confidence=1.0,
                    reason="用户明确给出唯一旧文本和新文本；Skill 完成合同在读取当前版本后生成受审批保护的 Diff 提案。",
                    arguments=arguments,
                    depends_on=[],
                    can_parallel=False,
                )
            )
            plan.should_use_tools = True
            plan.trace_events.append(
                {
                    "type": "tool_completion_contract",
                    "status": "supplemented",
                    "skill_key": skill_context.skill_key if skill_context else None,
                    "required_tool_key": tool_key,
                    "upstream_call_id": str(metadata.get("call_id") or "").strip(),
                    "evidence_reference": "prior_round_verified_file_revision",
                }
            )
            changed = True
            continue
        if tool_key != "workspace.files.read":
            continue
        candidate = next(
            (
                item
                for item in observations
                if isinstance(item, dict)
                and item.get("source_type") in {"workspace_file_search", "workspace_file_list"}
                and isinstance(item.get("metadata"), dict)
                and str(item["metadata"].get("file_id") or "").strip()
            ),
            None,
        )
        if candidate is None:
            continue
        metadata = candidate["metadata"]
        source_call_id = str(metadata.get("call_id") or "").strip()
        arguments = {"file_id": str(metadata["file_id"]).strip()}
        if metadata.get("revision_id"):
            arguments["expected_revision_id"] = str(metadata["revision_id"])
        plan.calls.append(
            PlannedToolCall(
                call_id=f"contract_read_{uuid4().hex[:12]}",
                tool_key=tool_key,
                provider=definition.provider,
                category=definition.category,
                display_name=definition.display_name,
                confidence=1.0,
                reason="Skill 完成合同要求核对原文；系统基于已获得的 opaque file_id 补充只读读取步骤。",
                arguments=arguments,
                # `depends_on` 只描述同一份 ToolPlan 内的待执行 DAG 边。
                # 这里的搜索已经在上一轮成功完成，file_id 已由服务端投影为
                # 受限事实；跨轮继续携带依赖会被 Workflow 误判为未完成节点。
                depends_on=[],
                can_parallel=False,
            )
        )
        plan.should_use_tools = True
        plan.trace_events.append(
            {
                "type": "tool_completion_contract",
                "status": "supplemented",
                "skill_key": skill_context.skill_key if skill_context else None,
                "required_tool_key": tool_key,
                # 不使用 call_id 字段，避免通用 Trace 持久化器将此审计引用
                # 误识别为一条独立的工具调用。
                "upstream_call_id": source_call_id,
                "evidence_reference": "prior_round_verified_opaque_file_id",
            }
        )
        changed = True
    return changed
