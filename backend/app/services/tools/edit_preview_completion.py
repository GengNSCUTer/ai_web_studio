"""文件编辑预览的业务完成判断，只有同文件同版本的真实预览才算完成。"""

import re
from typing import Any

from app.services.tools.file_edit_intent import preview_requested, approval_requested


def preview_requirement(query: str) -> dict[str, Any] | None:
    if not preview_requested(query):
        return None
    return {
        "completion_strategy": "required_tools",
        "required_tool_keys": ["workspace.files.read", "workspace.files.propose_edit"],
        "prerequisite_tool_keys": ["workspace.files.list", "workspace.files.search", "workspace.files.read"],
    }


def approval_requirement(query: str) -> dict[str, Any] | None:
    if not approval_requested(query):
        return None
    return {
        "completion_strategy": "required_tools",
        "required_tool_keys": ["workspace.files.read", "workspace.files.apply_edit"],
        "prerequisite_tool_keys": ["workspace.files.list", "workspace.files.search", "workspace.files.read"],
    }


def missing_approval_tools(observations: list[dict]) -> tuple[str, ...]:
    reads = {item.get("metadata", {}).get("file_id") for item in observations
             if item.get("source_type") == "workspace_file_read" and item.get("metadata", {}).get("tool_key") == "workspace.files.read"}
    if not reads:
        return ("workspace.files.read", "workspace.files.apply_edit")
    for item in observations:
        meta = item.get("metadata") or {}
        if meta.get("tool_key") != "workspace.files.apply_edit" or meta.get("file_id") not in reads:
            continue
        if item.get("source_type") == "workspace_file_edit_approval" and meta.get("approval_id") and meta.get("run_id"):
            return ()
        if item.get("source_type") == "workspace_file_revision" and meta.get("applied") is True:
            return ()
    return ("workspace.files.apply_edit",)


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

APPROVAL_GUIDANCE = (
    "用户本轮明确申请正式文件修改审批，不是再次索要只读预览。必须先read核对目标原文和版本，"
    "再调用workspace.files.apply_edit。ask权限下首次调用只会创建持久Diff与审批卡片，不直接写入。"
    "必须收到实际approval_id/tool_confirmation_required才可声称已创建审批；文字建议、list/search或propose_edit不能代替审批。"
    "标题指正文标题，不是重命名文件。old_string/new_string逐字保留空白，不含展示行号；expected_revision_id来自同次read。"
    "用户明确指定old_string/new_string字面参数时必须逐字沿用，不要擅自扩大为整行替换；原文不匹配则澄清。"
    "只有用户点击真实审批卡片，服务端通过权限与CAS，才能写入。用户聊天说‘确认’本身不是审批令牌。"
)
