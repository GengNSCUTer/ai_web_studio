"""从用户原问题与已审核Skill提取有限完成条件，不生成权限或工具参数。"""

from dataclasses import dataclass

from app.services.tools.completion_contract import infer_read_completion, contract_applies, _effective_required_tool_keys
from app.services.tools.edit_preview_completion import preview_requirement, approval_requirement


@dataclass(frozen=True)
class TaskSpec:
    goal: str
    required_tools: tuple[str, ...]
    evidence_mode: str
    # 当前仅核对工具证据；不等价于报告、引用或自然语言结论已全部正确。
    assessment_scope: str = "declared_tool_evidence"


def build_task_spec(*, query: str, skill_context=None) -> TaskSpec | None:
    """仅复用已有意图及审核条件；无法识别时明确不评估，不能默认完成。"""
    preview, approval = preview_requirement(query), approval_requirement(query)
    contract = preview or approval or (
        skill_context.completion_contract if skill_context else infer_read_completion(query))
    if skill_context and not preview and not approval and not contract_applies(query=query, contract=contract):
        return None
    required = _effective_required_tool_keys(query=query, contract=contract)
    if not required:
        return None
    return TaskSpec(goal=query, required_tools=required,
        evidence_mode="preview" if preview else "approval" if approval else "required_tools")
