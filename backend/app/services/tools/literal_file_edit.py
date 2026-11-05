"""明确字面替换的代码规划；只构造现有工具调用，不授予写入权限。"""

from dataclasses import dataclass, field
import re

from app.services.tools.file_edit_intent import approval_requested, preview_requested

NON_EXECUTING = re.compile(r'假设|如果|示例|例子|示范|只解释|仅解释|不要执行|不执行|不要申请|不用申请|禁止修改|不要修改|\b(?:example|hypothetical|do not execute)\b', re.I)


def _file_names(text):
    names = set(re.findall(r'(?<![A-Za-z0-9_./\\-])([A-Za-z0-9_.-]+\.(?:md|txt|py|json|ya?ml|docx?))(?![A-Za-z0-9_.-])', text, re.I))
    names.update(re.findall(r'[“‘"\x27`]([^/\\\n“”‘’"\x27`]+\.(?:md|txt|py|json|ya?ml|docx?))[”’"\x27`]', text, re.I))
    return names


@dataclass(frozen=True)
class LiteralFileEdit:
    file_name: str
    old: str
    new: str
    preview: bool


@dataclass(frozen=True)
class EditStep:
    tool_key: str | None
    reason: str
    arguments: dict = field(default_factory=dict)
    need_more: bool = False


def _quoted(text, start):
    pairs = {'“': '”', '‘': '’', '"': '"', "'": "'", '`': '`'}
    if start >= len(text) or text[start] not in pairs:
        return None
    end = text.find(pairs[text[start]], start + 1)
    if end < 0:
        return None
    value = text[start + 1:end]
    # 转义或嵌套引号可能改变字面含义，留给通用Planner澄清。
    if '\\' in value:
        return None
    return value, end + 1


def parse_literal_edit(query: str) -> LiteralFileEdit | None:
    """仅识别一份明确文件及一组带引号的原文/替换文本，不推断改写意图。"""
    preview = preview_requested(query)
    if not (preview or approval_requested(query)):
        return None
    if NON_EXECUTING.search(query):
        return None
    matches = list(re.finditer(r'(?:把|将|\breplace\s+)[ \t]*[“‘"\x27`]', query, re.I))
    if len(matches) != 1:
        return None
    old = _quoted(query, matches[0].end() - 1)
    if not old:
        return None
    connector = re.match(r'\s*(?:替换为|替换成|改为|改成|with)\s*', query[old[1]:], re.I)
    new = _quoted(query, old[1] + connector.end()) if connector else None
    if not new or not old[0] or old[0] == new[0] or max(len(old[0]), len(new[0])) > 12000:
        return None
    # 排除替换文本中的文件名，避免把正文里的另一个.md误当成操作目标。
    target_text = query[:matches[0].start()] + query[new[1]:]
    names = _file_names(target_text)
    if len(names) != 1:
        return None
    if '..' in next(iter(names)):
        return None
    return LiteralFileEdit(next(iter(names)), old[0], new[0], preview)


def parse_edit_discovery(query):
    """已明确单一文件的复杂编辑也可先定位/读取；old为空时绝不由代码猜编辑参数。"""
    if not (preview_requested(query) or approval_requested(query)) or NON_EXECUTING.search(query):
        return None
    names = _file_names(query)
    if len(names) != 1 or '..' in next(iter(names)):
        return None
    return LiteralFileEdit(next(iter(names)), '', '', preview_requested(query))


def next_edit_step(request: LiteralFileEdit, query, observations, candidate_keys) -> EditStep | None:
    """仅使用本次规范观察中的ID、可用原文与版本；歧义/缺失不猜测、不重复试探。"""
    edit_key = 'workspace.files.propose_edit' if request.preview else 'workspace.files.apply_edit'
    target_reads = [item for item in observations if item.get('source_type') == 'workspace_file_read'
                    and (item.get('metadata') or {}).get('tool_key') == 'workspace.files.read'
                    and (item.get('metadata') or {}).get('file_name') == request.file_name]
    read_pairs = {(item['metadata'].get('file_id'), item['metadata'].get('revision_id')) for item in target_reads
                  if item['metadata'].get('file_id') and item['metadata'].get('revision_id')}
    complete = any(
        (meta := item.get('metadata') or {}).get('tool_key') == edit_key
        and any(meta.get('file_id') == pair[0] for pair in read_pairs)
        and ((request.preview and item.get('source_type') == 'workspace_file_edit_preview'
              and (meta.get('file_id'), meta.get('revision_id')) in read_pairs)
             or (not request.preview and item.get('source_type') == 'workspace_file_edit_approval'
                 and meta.get('approval_id') and meta.get('run_id'))
             or (not request.preview and item.get('source_type') == 'workspace_file_revision' and meta.get('applied') is True))
        for item in observations)
    if complete:
        return EditStep(None, 'literal_edit_completed')
    if any(item.get('source_type') in {'tool_error_feedback', 'tool_quality_feedback'} for item in observations):
        return EditStep(None, 'literal_edit_tool_refused')
    reads = [item for item in observations if item.get('source_type') == 'workspace_file_read'
             and (item.get('metadata') or {}).get('tool_key') == 'workspace.files.read']
    if reads:
        reads = [item for item in reads if (item.get('metadata') or {}).get('file_name') == request.file_name]
        if len(reads) != 1 or reads[0].get('excerpt_status') != 'available':
            return EditStep(None, 'literal_edit_unavailable')
        item, meta = reads[0], reads[0]['metadata']
        text = item.get('excerpt')
        if not isinstance(text, str) or not meta.get('file_id') or not meta.get('revision_id'):
            return EditStep(None, 'literal_edit_unavailable')
        if not request.old:
            # 复杂改写只在真实原文可见后交给LLM生成参数，不重复请模型定位同一文件。
            return None
        if text.count(request.old) != 1:
            return EditStep(None, 'literal_edit_ambiguous' if text.count(request.old) > 1 else 'literal_edit_not_found')
        if edit_key not in candidate_keys:
            return EditStep(None, 'literal_edit_not_allowed')
        # 全文唯一性与当前版本仍由Executor/审批服务检查，片段不能证明全文唯一。
        return EditStep(edit_key, 'literal_edit_verified', {'file_id': meta['file_id'],
            'expected_revision_id': meta['revision_id'], 'old_string': request.old, 'new_string': request.new})
    searches = [item for item in observations if item.get('source_type') == 'workspace_file_search'
                and (item.get('metadata') or {}).get('tool_key') == 'workspace.files.search']
    if searches:
        ids = {item['metadata'].get('file_id') for item in searches} - {None, ''}
        incomplete = any(str(item['metadata'].get(key, '')).lower() == 'true'
                         for item in searches for key in ('has_more', 'results_truncated'))
        if len(ids) != 1 or incomplete:
            return EditStep(None, 'literal_edit_ambiguous' if ids else 'literal_edit_not_found')
        if 'workspace.files.read' not in candidate_keys:
            return EditStep(None, 'literal_edit_not_allowed')
        return EditStep('workspace.files.read', 'literal_edit_read', {'file_id': next(iter(ids))}, True)
    if 'workspace.files.search' not in candidate_keys:
        return EditStep(None, 'literal_edit_not_allowed')
    return EditStep('workspace.files.search', 'literal_edit_locate',
                    {'query': request.file_name, 'file_name': request.file_name}, True)
