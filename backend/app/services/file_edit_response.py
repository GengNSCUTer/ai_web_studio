"""编辑模式的状态答复由规范执行记录生成，模型不再替审批系统报告结果。"""

import re
from app.services.tools.file_edit_intent import preview_requested, approval_requested


def build_file_edit_response(*, query, mode, sources, final_source_map, events):
    if mode != 'edit_proposal' or not (preview_requested(query) or approval_requested(query)):
        return None
    statements = []
    # 只从本轮Workspace执行器的规范来源取状态，不解析文件正文里声称的审批ID。
    for source in sources:
        kind, meta = source.source_type, source.metadata or {}
        label = source.citation_label
        final = final_source_map.get(label)
        citation = f' {label}' if final else ''
        if (kind == 'workspace_file_revision' and meta.get('tool_key') == 'workspace.files.apply_edit'
                and meta.get('applied') is True and meta.get('revision_id')):
            statements.append('本轮修改已按当前工作区授权应用，已保存新的文件版本。' + citation)
        elif (kind == 'workspace_file_edit_approval' and meta.get('tool_key') == 'workspace.files.apply_edit'
              and meta.get('approval_id') and meta.get('run_id')):
            statements.append('本轮已创建真实的修改审批，尚未因这份审批写入文件。请核对工具卡片中的 Diff，再点击“确认并应用 Diff”或“拒绝”。' + citation)
        elif kind == 'workspace_file_edit_preview' and meta.get('tool_key') == 'workspace.files.propose_edit':
            if final and (final.get('metadata') or {}).get('citation_body_chars', 0) > 0:
                text = final['display_text']
                fence = '`' * max(3, max((len(run) + 1 for run in re.findall(r'`+', text)), default=0))
                statements.append('已生成经服务端校验的 Diff 预览；本轮未修改文件，预览也不代表批准写入。' + citation
                                  + f'\n\n{fence}diff\n{text}\n{fence}')
            else:
                statements.append('工具已生成预览，但本轮展示预算未保留可用的 Diff 正文；请打开工具过程查看或缩小修改范围后重试。本轮未写入文件。')
    if statements:
        return '\n\n'.join(dict.fromkeys(statements))
    reasons = {event.payload.get('reason') for event in events if event.type == 'tool_planner_end'}
    if 'literal_edit_ambiguous' in reasons:
        reason = '目标文件或原文存在多处匹配，请提供足以唯一定位的上下文。'
    elif 'literal_edit_not_found' in reasons:
        reason = '未找到目标文件或指定的原文片段，请核实文件名与原文。'
    elif any(event.type == 'tool_policy_check' and event.payload.get('status') == 'denied' for event in events):
        reason = '当前工作区权限不允许这次修改，请核对只读策略或授权。'
    elif 'literal_edit_unavailable' in reasons:
        reason = '当前可用原文不足，无法核实这次替换，请补充可读取的准确片段。'
    else:
        reason = '本轮未能完成所需工具步骤，请核对目标与替换内容后重试。'
    return reason + (' 本轮未生成经过校验的 Diff 预览，也未写入文件。' if preview_requested(query)
                     else ' 本轮没有创建真实审批，也未执行文件写入；聊天中的修改请求不能代替审批确认。')
