"""从用户原问题与已审核Skill提取有限完成条件，不生成权限或工具参数。"""

from dataclasses import dataclass
import re

from app.services.tools.completion_contract import infer_read_completion, contract_applies, _effective_required_tool_keys
from app.services.tools.edit_preview_completion import preview_requirement, approval_requirement


@dataclass(frozen=True)
class TaskSpec:
    goal: str
    required_tools: tuple[str, ...]
    evidence_mode: str
    # 当前仅核对工具证据；不等价于报告、引用或自然语言结论已全部正确。
    assessment_scope: str = "declared_tool_evidence"
    required_files: tuple[str, ...] = ()


def explicit_file_targets(query: str) -> tuple[str, ...]:
    """只取原始任务的显式ID和读取子句里的字面文件名，不读工具正文推导新目标。"""
    targets = ["file_id:" + value for value in re.findall(
        r"(?<![A-Za-z0-9_])file[_ -]?id\s*(?:是|为|[:=：])\s*([a-zA-Z0-9_-]{1,64})", query, re.I)]
    quoted = list(re.finditer(r'“[^”]*”|‘[^’]*’|"[^"\n]*"|\x27[^\x27\n]*\x27|`[^`\n]*`', query))
    for match in re.finditer(r"(?:读取|阅读|核对|审阅|比较|对比|\bread\b|\binspect\b|\bcompare\b)([^，。；\n]{1,600})", query, re.I):
        if any(span.start() < match.start() < span.end() for span in quoted):
            continue
        if re.search(r"(?:不要|禁止|不用|无需|不需要|do not|don't|never)\s*$", query[max(0, match.start()-12):match.start()], re.I):
            continue
        clause = re.split(r"然后|之后|并?(?:生成|导出|另存|保存|写入)|后[，,]?|\b(?:then|export|save|generate|write)\b", match[1], maxsplit=1, flags=re.I)[0]
        names = re.findall(r"(?<![A-Za-z0-9_./\\-])([A-Za-z0-9_.-]+\.(?:md|txt|pdf|docx?))(?![A-Za-z0-9_-]|\.[A-Za-z0-9])", clause, re.I)
        names += re.findall(r'[“‘"\x27`]([^/\\\n“”‘’"\x27`]+\.(?:md|txt|pdf|docx?))[”’"\x27`]', clause, re.I)
        targets.extend("file_name:" + name for name in names if ".." not in name)
    return tuple(dict.fromkeys(targets))


def missing_file_targets(spec: TaskSpec | None, sources: list) -> tuple[str, ...]:
    """读取必须对应目标资料；搜索、空结果、节选或低质量证据不能替代正文。"""
    if spec is None or not spec.required_files:
        return ()
    available = set()
    for source in sources:
        meta = source.metadata
        if (source.source_type != "workspace_file_read" or meta.get("tool_key") != "workspace.files.read"
                or not source.used_in_prompt or not source.display_text.strip() or meta.get("empty_reason")
                or meta.get("quality_status", "valid") != "valid" or meta.get("evidence_pretruncated")
                or meta.get("citation_body_truncated")):
            continue
        for key in ("file_id", "file_name"):
            if meta.get(key):
                available.add(f"{key}:{meta[key]}")
    return tuple(target for target in spec.required_files if target not in available)


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
        evidence_mode="preview" if preview else "approval" if approval else "required_tools",
        required_files=explicit_file_targets(query) if not preview and not approval and "workspace.files.read" in required else ())
