"""仅解释用户原问题的文件编辑意图，不从工具正文获得写入授权。"""

import re


FILE_HINT = re.compile(r"文件|工作区|项目文档|\.(?:md|txt|py|json|ya?ml|docx?)(?=$|[^A-Za-z0-9_])|\b(?:file|workspace)\b", re.I)
EDIT_ACTION = re.compile(r"修改|编辑|替换|重构|diff|补丁|写入|改成|改为|更改|\b(?:edit|modify|replace|rewrite|patch|write)\b", re.I)
PREVIEW = re.compile(r"提案|草稿|预览|diff|\b(?:proposal|preview|draft|patch)\b", re.I)


def preview_requested(query: str) -> bool:
    """明确要求文件修改预览时走无写入能力；普通报告草稿不属于文件编辑。"""

    if re.search(r"(?:不要|不用|无需|别)(?:生成|提供|做)?(?:修改)?(?:提案|草稿|预览|diff)|\b(?:no|without)\s+(?:draft|preview|proposal)\b", query, re.I):
        return False
    return bool(FILE_HINT.search(query) and PREVIEW.search(query) and EDIT_ACTION.search(query))


def edit_requested(query: str) -> bool:
    """禁止实际写入不等于禁止生成只读Diff，其他否定编辑指令仍然生效。"""

    if preview_requested(query):
        return True
    text = re.sub(r"(?:不要|不用|别|无需|不)(?:实际|直接)?(?:修改|编辑|替换|写入)", "", query)
    text = re.sub(r"\b(?:do not|don't|never)\s+(?:edit|modify|replace|rewrite|write)(?:\s+files)?\b", "", text, flags=re.I)
    return bool(EDIT_ACTION.search(text))


def approval_requested(query: str) -> bool:
    """用户明确申请文件修改审批，不把上一轮只读预览自动变成写入授权。"""

    return bool(FILE_HINT.search(query) and edit_requested(query) and not preview_requested(query)
                and re.search(r"审批|正式申请|申请修改|受控修改|确认后.{0,8}(?:修改|写入)|\b(?:approval|approve)\b", query, re.I))
