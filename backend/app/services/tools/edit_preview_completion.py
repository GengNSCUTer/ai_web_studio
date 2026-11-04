"""文件编辑预览的业务完成判断，只有同文件同版本的真实预览才算完成。"""

import re
from typing import Any

from app.services.tools.file_edit_intent import preview_requested


def preview_requirement(query: str) -> dict[str, Any] | None:
    if not preview_requested(query):
        return None
    return {
        "completion_strategy": "required_tools",
        "required_tool_keys": ["workspace.files.read", "workspace.files.propose_edit"],
        "prerequisite_tool_keys": ["workspace.files.list", "workspace.files.search", "workspace.files.read"],
    }


def missing_preview_tools(query: str, observations: list[dict]) -> tuple[str, ...]:
    """来源必须是执行器规范化结果；文字草稿与错误反馈均不能满足合同。"""

    names = set(re.findall(r"[A-Za-z0-9_.-]+\.(?:md|txt|py|json|ya?ml|docx?)(?=$|[^A-Za-z0-9_])", query, re.I))
    names.update(re.findall(r"[\"'`“]([^\"'`”]+\.(?:md|txt|py|json|ya?ml|docx?))[\"'`”]", query, re.I))
    reads: dict[tuple[str, str], dict] = {}
    for item in observations:
        meta = item.get("metadata") or {}
        if item.get("source_type") != "workspace_file_read" or meta.get("tool_key") != "workspace.files.read":
            continue
        if not meta.get("file_id") or not meta.get("revision_id") or meta.get("empty_reason"):
            continue
        if names and meta.get("file_name") not in names:
            continue
        reads[(meta["file_id"], meta["revision_id"])] = meta
    if not reads:
        return ("workspace.files.read", "workspace.files.propose_edit")
    previews = {
        (meta.get("file_id"), meta.get("revision_id"))
        for item in observations
        if item.get("source_type") == "workspace_file_edit_preview"
        and (meta := item.get("metadata") or {}).get("tool_key") == "workspace.files.propose_edit"
    }
    # 未明确多文件时，一个经读取确认的目标预览即可；多个明示文件必须逐个覆盖。
    if names:
        covered = {meta.get("file_name") for pair, meta in reads.items() if pair in previews}
        complete = names.issubset(covered)
    else:
        complete = bool(previews.intersection(reads))
    return () if complete else ("workspace.files.propose_edit",)


PREVIEW_GUIDANCE = (
    "用户要求的是文件内容修改预览：必须先read读取目标原文，再调用workspace.files.propose_edit取得服务端校验的Diff。"
    "list/search不是正文，模型自行写出的Diff不是已验证预览。标题指文件正文中的标题，不是重命名文件。"
    "old_string必须逐字复制excerpt中的原文，保留空格和换行，不添加展示行号、引号或省略号；"
    "用同一次读取的file_id/revision_id（expected_revision_id）生成预览。"
    "参数不匹配时读取有关行再修正，目标不明确时澄清；不得调用apply_edit或声称已写入。"
)
