"""真实浏览器验证语义候选、在线 Planner、工具、回答及持久化闭环。"""

import json
import os
import re
from pathlib import Path
import time

import httpx

from playwright.sync_api import expect, sync_playwright
from sqlalchemy import select

from stage46a_real_e2e import cleanup, prepare_fixture
from app.core.database import SessionLocal
from app.models import Conversation, Message, ProjectFile, ToolCallRun, ToolRouteRun

BASE_URL = os.getenv('AIWS_E2E_BASE_URL', 'http://127.0.0.1:32018')
TIMEOUT_MS = int(os.getenv('AIWS_E2E_TIMEOUT_MS', '240000'))
CASES = (
    ('semantic_weather', "Is it raining in 深圳 right now? Look up current weather and temperature. Don't search the web.", 'amap.maps.weather'),
    ('semantic_attachment', 'Find the uploaded note explaining retention. Read it and answer with its project name and the number of days. Do not search the web.', 'workspace.files'),
    ('explicit_no_tools', 'Do not use any tools. Just reply received.', None),
)


def collect(project_id, file_id, scenario, required):
    with SessionLocal() as db:
        conversation = db.scalars(select(Conversation).where(Conversation.project_id==project_id)
                                  .order_by(Conversation.created_at.desc()).limit(1)).one()
        route = db.scalars(select(ToolRouteRun).where(ToolRouteRun.conversation_id==conversation.id)
                           .order_by(ToolRouteRun.created_at.desc()).limit(1)).one()
        answer = db.get(Message, route.assistant_message_id)
        if answer.status != 'done' or conversation.model_name != 'deepseek-ai/DeepSeek-V3.2':
            raise AssertionError('最终消息或在线模型状态不符合预期。')
        events = json.loads(route.events_json or '[]')
        candidates = [event for event in events if event.get('type')=='tool_candidate_selection']
        calls = list(db.scalars(select(ToolCallRun).where(ToolCallRun.route_run_id==route.id)))
        if required:
            if not candidates or candidates[0].get('retrieval',{}).get('mode')!='hybrid':
                raise AssertionError(f'{scenario}: 实际 Chat 没有完成语义候选召回。')
            if not any(event.get('strategy')=='llm_primary' for event in events if event.get('type')=='tool_planner_end'):
                raise AssertionError(f'{scenario}: 在线 Planner 没有成功。')
            if required=='amap.maps.weather':
                weather = [call for call in calls if call.tool_key==required and call.status=='success']
                if not weather:
                    raise AssertionError(f'{scenario}: 没有真实有效天气结果，calls={[(c.tool_key,c.status) for c in calls]}')
                sources = [source for call in weather for source in json.loads(call.sources_json or '[]')]
                temperatures = [re.findall(r'-?\d+(?:\.\d+)?',str(source.get('metadata',{}).get('raw',{}).get('temperature',''))) for source in sources]
                if not any(values and all(re.search(r'(?<!\d)'+re.escape(value)+r'(?!\d)',answer.content) for value in values) for values in temperatures):
                    raise AssertionError(f'温度事实核对：expected={temperatures}, answer={answer.content[:650]}')
                if all(source.get('metadata',{}).get('raw',{}).get('weather_data_type')=='forecast' for source in sources):
                    if not re.search(r'forecast|预报',answer.content,re.I):
                        raise AssertionError('仅取得预报却未告知最终回答的时效限制。')
            else:
                sources = [source for call in calls if call.status=='success' for source in json.loads(call.sources_json or '[]')]
                if not any(source.get('metadata',{}).get('file_id')==file_id for source in sources):
                    raise AssertionError('没有读取当前项目的目标文件证据。')
                if 'CedarLedger' not in answer.content or '17' not in answer.content:
                    raise AssertionError('最终回答没有忠实使用文件中的项目名称及周期。')
            if any(call.tool_key.startswith(('web.','docs.')) or call.tool_key.endswith(('propose_edit','apply_edit')) for call in calls):
                raise AssertionError('执行了被禁止的网络/编辑工具。')
        else:
            if calls or not candidates or candidates[0].get('selected_count')!=0 or 'received' not in answer.content.lower():
                raise AssertionError('明确无需工具仍执行了工具或回答不符合预期。')
        return dict(scenario=scenario, answer_status=answer.status, tool_keys=[call.tool_key for call in calls],
                    source_checked=True, elapsed_ms=route.elapsed_ms,
                    selector=candidates[0].get('selector'), retrieval=candidates[0].get('retrieval'),
                    candidates=[c['tool_key'] for c in candidates[0]['candidates']],
                    weather_answer=answer.content[:650] if scenario=='semantic_weather' else None)


def run():
    # 服务重启需完成 Schema 初始化，不能把短暂未就绪误判成登录失效。
    for attempt in range(20):
        try:
            response = httpx.get(os.getenv('AIWS_E2E_BACKEND_URL','http://127.0.0.1:32017')+'/health', timeout=2)
            if response.status_code == 200:
                break
        except httpx.RequestError:
            pass
        time.sleep(.5)
    else:
        raise AssertionError('后端未就绪，未开始浏览器业务验收。')
    token,user_id,project_id,file_id,_ = prepare_fixture()
    errors=[]
    try:
        with SessionLocal() as db:
            file=db.get(ProjectFile,file_id)
            file.file_name='archive-notes.md'
            file.parsed_text='# CedarLedger retention note\nProject: CedarLedger.\nRetention period: 17 days.\nRead only.\n'
            db.commit()
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            try:
                context=browser.new_context(viewport={'width':1440,'height':1000})
                context.add_cookies([{'name':'aiws_token','value':token,'url':BASE_URL,'httpOnly':True,'sameSite':'Lax'}])
                page=context.new_page()
                page.on('pageerror',lambda error:errors.append(type(error).__name__))
                page.on('console',lambda message:errors.append('console_error') if message.type=='error' else None)
                page.goto(BASE_URL+'/chat',wait_until='networkidle',timeout=60000)
                expect(page.get_by_placeholder('输入你的问题')).to_be_visible(timeout=30000)
                page.locator('select').nth(0).select_option(project_id)
                rows=[]
                for scenario,question,required in CASES:
                    page.get_by_role('button',name='新对话').click()
                    page.get_by_test_id('tool-run-mode-selector').select_option('workspace_review')
                    toggle=page.get_by_role('button',name='联网搜索',exact=True)
                    if 'is-active' not in (toggle.get_attribute('class') or ''):
                        toggle.click()
                    page.get_by_placeholder('输入你的问题').fill(question)
                    page.get_by_role('button',name='发送消息').click()
                    page.wait_for_function("() => document.querySelector('button[type=submit]')?.disabled === true",timeout=10000)
                    page.wait_for_function("() => ['发送消息','Send'].includes(document.querySelector('button[type=submit]')?.textContent?.trim())",timeout=TIMEOUT_MS)
                    page.wait_for_load_state('networkidle')
                    row=collect(project_id,file_id,scenario,required)
                    rows.append(row)
                    print(json.dumps(row,ensure_ascii=False),flush=True)
                if errors:
                    raise AssertionError(f'浏览器发现 {len(errors)} 个错误。')
                page.screenshot(path='/tmp/tool-semantic-e2e.png',full_page=True)
                report={'passed':len(rows),'browser_errors':0,'model':'deepseek-ai/DeepSeek-V3.2','cases':rows}
                print(json.dumps(report,ensure_ascii=False))
            finally:
                browser.close()
    finally:
        cleanup(user_id=user_id,project_id=project_id)
    report['temporary_data_cleaned'] = True
    output = Path(__file__).resolve().parents[1]/'backend/evals/results/tool_semantic_e2e_2026-10-11.json'
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')


if __name__=='__main__':
    run()
