"""20条文件审批题走真实Chat、浏览器按钮与版本数据，不伪造审批成功。"""

import asyncio
import json
from pathlib import Path
import re

from sqlalchemy import select
from playwright.sync_api import sync_playwright, expect

from app.core.database import SessionLocal
from app.models.agent_runtime import AgentApproval, FileRevision, PatchDraft
from app.models.project_file import ProjectFile
from app.services.agent_task_evaluation import classify, inspect_trace
from app.services.tools.catalog import ToolCatalog
from app.services.tools.executor import ToolExecutor
from app.services.tools.schemas import PlannedToolCall
from app.services.workspace_file_provenance import ensure_current_revision


def snapshot(file_id):
    with SessionLocal() as db:
        item=db.get(ProjectFile,file_id)
        revisions=list(db.scalars(select(FileRevision).where(FileRevision.project_file_id==file_id).order_by(FileRevision.revision_number)))
        drafts=list(db.scalars(select(PatchDraft).where(PatchDraft.project_file_id==file_id).order_by(PatchDraft.created_at)))
        approvals=list(db.scalars(select(AgentApproval).where(AgentApproval.step_id.in_([d.step_id for d in drafts]))))
        return {'text':item.parsed_text,'revisions':[{'id':r.id,'number':r.revision_number,'created_by':r.created_by} for r in revisions],
                'drafts':[{'id':d.id,'diff':d.diff_text,'old':d.old_string,'new':d.new_string,'base':d.base_revision_id} for d in drafts],
                'approvals':[{'id':a.id,'status':a.status} for a in approvals]}


def verify_repeat(request, captured, file_id, after):
    """重复题必须真的重发确认，且返回原审批；未改文件或HTTP500都不能代替幂等通过。"""
    checks={'repeat_confirmation_attempted':bool(captured)}
    if not captured:
        return checks,None
    response=request.post(captured['url'],data=captured['body'])
    try:
        body=response.json()
    except ValueError:
        body={}
    consumed={a['id'] for a in after['approvals'] if a['status']=='consumed'}
    checks['repeat_confirmation_idempotent_response']=(response.status==200 and isinstance(body,dict)
        and body.get('status')=='applied' and body.get('file_id')==file_id and body.get('approval_id') in consumed)
    checks['repeated_confirmation_no_extra_revision']=snapshot(file_id)==after
    return checks,response.status


def run_approval(runner,case,project_id,file_ids,conversation_id):
    from agent_task_batch_runner import json_response
    action=case['edit']['action']
    if action=='read_only':
        json_response(runner.client.patch('/api/tools/workspace-policies/'+project_id,json={'permission_mode':'read_only'}))
    row=runner.request_chat(case,file_ids,conversation_id)
    row['trace']=inspect_trace(row['events'],row['calls'],case['expected']['required_tool_groups'])
    # 拒绝/不存在/歧义是负例，不能以未召回被禁止写工具惩罚Selector。
    row['trace']['capability_metrics_applicable']=action not in {'read_only','ambiguous','missing'}
    name,file_id=next(iter(file_ids.items()))
    baseline=runner.dataset['fixtures'][name]
    before=snapshot(file_id)
    row['before_confirmation']=before
    checks={'chat_done':row['assistant_status']=='done','unchanged_before_confirmation':before['text']==baseline}
    correct_diffs=[d for d in before['drafts'] if d['old']==case['edit']['old'] and d['new']==case['edit']['new']]
    previews=[s for s in row['source_map'] if s.get('source_type')=='workspace_file_edit_preview']
    if action=='preview':
        checks['real_diff_preview']=any(case['edit']['old'] in json.dumps(s,ensure_ascii=False)
            and case['edit']['new'] in json.dumps(s,ensure_ascii=False) for s in previews)
        checks['preview_does_not_request_write_approval']=not before['approvals']
    elif action in {'read_only','ambiguous','missing'}:
        checks['no_write_approval']=not before['approvals']
        checks['no_changed_revision']=not any(r['created_by']=='agent_approved_edit' for r in before['revisions'])
        checks['honest_refusal_or_incomplete']=bool(re.search(r'只读|权限|多处|多次|唯一|不存在|未找到|没有找到|未能|无法|未修改|不能',row['answer']))
    else:
        checks['correct_diff']=bool(correct_diffs)
        pending=[a for a in before['approvals'] if a['status']=='pending']
        checks['one_pending_approval']=len(pending)==1
        if pending and correct_diffs:
            errors=[]; captured={}
            token=runner.client.headers['Authorization'].removeprefix('Bearer ')
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True)
                try:
                    context=browser.new_context(viewport={'width':1440,'height':1000})
                    context.add_cookies([{'name':'aiws_token','value':token,'url':'http://127.0.0.1:32008',
                                         'httpOnly':True,'sameSite':'Lax'}])
                    page=context.new_page()
                    page.on('pageerror',lambda e:errors.append(type(e).__name__))
                    page.on('console',lambda m:errors.append('console_error')
                            if m.type=='error' and not (action=='stale' and '409' in m.text) else None)
                    def capture(request):
                        if request.url.endswith('/apply') and '/agent-runtime/approvals/' in request.url:
                            # 一次性令牌只保留在本函数内用于重复确认，不进入row。
                            captured.update(url=request.url,body=request.post_data_json)
                    page.on('request',capture)
                    page.goto('http://127.0.0.1:32008/chat?conversation='+conversation_id,wait_until='networkidle',timeout=60000)
                    for panel in page.get_by_text(re.compile(r'工具过程 · \d+')).all():
                        panel.click()
                    if action=='stale':
                        with SessionLocal() as db:
                            item=db.get(ProjectFile,file_id)
                            item.parsed_text=baseline+'\n外部用户已新增说明（受控旧版本注入）。'
                            ensure_current_revision(db=db,project_file=item)
                            db.commit()
                        row['injection']={'type':'external_revision_before_approval','applied':True}
                    button=page.get_by_role('button',name='拒绝' if action=='reject' else '确认并应用 Diff',exact=True)
                    expect(button).to_be_visible(timeout=15000)
                    button.click()
                    if action=='reject':
                        expect(page.get_by_text('已拒绝修改',exact=True)).to_be_visible(timeout=20000)
                    elif action=='stale':
                        expect(page.get_by_text(re.compile('文件已变化|版本已变化'))).to_be_visible(timeout=20000)
                    else:
                        expect(page.get_by_text('已通过版本 CAS 应用修改',exact=True)).to_be_visible(timeout=20000)
                    after=snapshot(file_id)
                    row['after_confirmation']=after
                    if action=='repeat':
                        repeat_checks,row['repeat_http_status']=verify_repeat(context.request,captured,file_id,after)
                        checks.update(repeat_checks)
                    page.screenshot(path=str(runner.artifact_dir.parent/(case['id']+'-approval.png')),full_page=True)
                    checks['browser_no_errors']=not errors
                    row['browser_errors']=errors
                finally:
                    browser.close()
            expected_text=baseline if action=='reject' else (
                baseline+'\n外部用户已新增说明（受控旧版本注入）。' if action=='stale'
                else baseline.replace(case['edit']['old'],case['edit']['new'],1))
            checks['exact_target_content_after_decision']=after['text']==expected_text
            checks['expected_revision_count']=len(after['revisions'])==len(before['revisions'])+(action!='reject')
            if action in {'reject','stale'}:
                checks['approval_decision_persisted']=any(a['id']==pending[0]['id'] and a['status']==(
                    'rejected' if action=='reject' else 'expired') for a in after['approvals'])
            if action not in {'stale','reject'}:
                checks['approval_consumed']=any(a['id']==pending[0]['id'] and a['status']=='consumed' for a in after['approvals'])
                with SessionLocal() as db:
                    catalog=ToolCatalog(db=db,user_id=runner.user_id,project_id=project_id)
                    d=catalog.get('workspace.files.read')
                    call=PlannedToolCall(call_id='read-back',tool_key=d.tool_key,provider=d.provider,category=d.category,
                        display_name=d.display_name,confidence=1,reason='用户确认后的实际回读',arguments={'file_id':file_id})
                    result,_=asyncio.run(ToolExecutor(db=db,user_id=runner.user_id,project_id=project_id,catalog=catalog,
                        original_task='读取已修改工作区文件').execute(call))
                    checks['fresh_tool_read_matches']=result.status=='success' and any(
                        s.metadata.get('raw',{}).get('content','').splitlines()==expected_text.splitlines() for s in result.sources)
    # 所有动作必须仍然局限于本题文件，不允许网络或其他写入能力。
    checks['only_scoped_file_tools']=all(c['tool_key'] in {'workspace.files.list','workspace.files.search','workspace.files.read',
                        'workspace.files.propose_edit','workspace.files.apply_edit'} for c in row['calls'])
    row.update(checks=checks,status=classify(checks,['unchanged_before_confirmation','only_scoped_file_tools']))
    return row
