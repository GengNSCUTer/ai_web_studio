"""固定账号在线摘要恢复验收；临时项目、三种历史长度及真实浏览器。"""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))

from playwright.sync_api import sync_playwright, expect
from sqlalchemy import insert, select
from app.core.database import SessionLocal
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.project import Project
from app.models.user import User
from app.models.user_setting import UserSetting
from app.repositories.user_repo import UserRepository
from app.services.auth_service import AuthService
from app.models.conversation_summary import ConversationSummaryJob
from app.repositories.message_repo import MessageRepository
from app.services.conversation_summary_service import ConversationSummaryService
from stage46a_real_e2e import cleanup

BASE = 'http://127.0.0.1:32008'


def control(action):
    result = subprocess.run(['bash','deploy/run_services.sh','ctl',action,'summary-worker'],
        cwd=ROOT, capture_output=True, text=True)
    if result.returncode and 'already started' not in result.stdout:
        raise RuntimeError('摘要Worker控制失败')


def poll(context, path, completed, *, timeout=360):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        response=context.request.get(BASE+'/api/backend'+path)
        assert response.ok, f'查询状态失败：{response.status}'
        value=response.json()
        if completed(value): return value
        time.sleep(.5)
    raise AssertionError('在线验收等待超时')


def run():
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email=='1528713326@qq.com'))
        assert user
        settings=db.scalar(select(UserSetting).where(UserSetting.user_id==user.id))
        assert settings and settings.provider_type!='ollama'
        uid, model=user.id, settings.default_model
        token=AuthService(UserRepository(db)).create_access_token(user)
        project=Project(user_id=uid,name='summary-e2e-'+uuid4().hex[:8])
        db.add(project)
        db.commit()
        pid=project.id
    errors, rows=[],[]
    try:
        control('stop')
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(headless=True)
            context=browser.new_context(viewport={'width':1440,'height':1000})
            context.add_cookies([{'name':'aiws_token','value':token,'url':BASE,'httpOnly':True,'sameSite':'Lax'}])
            page=context.new_page()
            page.on('pageerror', lambda error: errors.append(type(error).__name__))
            page.on('console', lambda message: errors.append('console_error') if message.type=='error' else None)
            fixtures=[]
            for count in (64,128,256):
                with SessionLocal() as db:
                    conv=Conversation(user_id=uid, project_id=pid, title=f'摘要恢复{count}',model_name=model,
                        memory_extraction_cursor=count+100, memory_scan_cursor=count+100)
                    db.add(conv);db.flush()
                    ids=[str(uuid4()) for _ in range(count)]
                    start=datetime.now(timezone.utc)-timedelta(hours=2)
                    db.execute(insert(Message),[dict(id=mid, conversation_id=conv.id, sequence=i+1,
                        role='user' if i%2==0 else 'assistant', status='done',
                        content=('已废弃口令：棕熊502。' if i==0 else '本项目唯一验收编号是海鸥731，请保留这个事实。' if i==2
                            else f'这是第{i+1}条普通工作记录，没有新的验收编号。'),
                        created_at=start+timedelta(seconds=i)) for i,mid in enumerate(ids)])
                    conv.context_summary='已废弃口令棕熊502；验收编号海鸥731。'
                    conv.context_summary_boundary_message_id=ids[count-25]
                    db.commit()
                    fixtures.append((count,conv.id,ids[0]))
                response=context.request.delete(f'{BASE}/api/backend/conversations/{conv.id}/messages/{ids[0]}')
                assert response.status==204
            # 先暂停修复Worker，确保真实Chat展示恢复提示，而不是误生成缺失历史的答案。
            count,cid,_=fixtures[0]
            page.goto(BASE+'/chat?conversation='+cid,wait_until='networkidle')
            composer=page.get_by_placeholder('输入你的问题')
            composer.fill('只根据本会话历史，告诉我项目唯一验收编号是什么。不要调用工具。')
            page.get_by_role('button',name='发送消息',exact=True).click()
            expect(page.get_by_text('较早对话正在后台恢复摘要，本轮尚未生成回答。请稍后重新生成。',exact=True)).to_be_visible(timeout=120000)
            page.reload(wait_until='networkidle')
            expect(page.get_by_text('较早对话正在后台恢复摘要，本轮尚未生成回答。请稍后重新生成。',exact=True)).to_be_visible()
            page.screenshot(path='/tmp/aiws-summary-rebuilding.png',full_page=True)
            control('start')
            for count,cid,_ in fixtures:
                began=time.monotonic()
                snapshot=poll(context,f'/conversations/{cid}/summary-rebuild',
                    lambda value:value['job'] is not None and value['job']['status'] in {'succeeded','failed'})
                assert snapshot['job']['status']=='succeeded',snapshot['job']['error_code']
                with SessionLocal() as db:
                    summary=db.get(Conversation,cid).context_summary
                    assert '棕熊502' not in summary
                    assert '731' in summary
                created=context.request.post(BASE+'/api/backend/chat/runs',data={'conversation_id':cid,
                    'content':'只根据本会话历史，简短回答项目唯一验收编号，不要调用工具。',
                    'web_search_enabled':False,'tool_run_mode':'quick_chat'})
                assert created.status==202
                answer=poll(context,'/chat/runs/'+created.json()['id'],
                    lambda value:value['status'] in {'succeeded','failed','cancelled','expired'})
                assert answer['status']=='succeeded',answer['error_code']
                assert '731' in answer['content'] and '棕熊502' not in answer['content']
                rows.append({'messages':count,'batches':snapshot['job']['batches'],
                    'estimated_input_tokens':snapshot['job']['input_tokens'],
                    'estimated_output_tokens':snapshot['job']['output_tokens'],
                    'observed_wait_and_answer_seconds':round(time.monotonic()-began,2),
                    'retained_fact':True,'deleted_fact_absent':True,'answer_status':answer['status']})
            page.goto(BASE+'/chat?conversation='+fixtures[-1][1],wait_until='networkidle')
            expect(page.locator('article.message-assistant').last).to_contain_text('731')
            page.screenshot(path='/tmp/aiws-summary-rebuilt.png',full_page=True)
            assert not errors,errors
            browser.close()
        print(json.dumps({'cases':rows,'rebuilding_hint':True,'browser_errors':len(errors),
            'provider_model':model,'token_counts':'tokenizer估算，非Provider计费usage'},ensure_ascii=False))
    finally:
        control('start')
        cleanup(user_id=uid,project_id=pid)


def restart_smoke():
    """只跑真实进程重启场景，不重复三条浏览器/回答用例。"""
    with SessionLocal() as db:
        user=db.scalar(select(User).where(User.email=='1528713326@qq.com'))
        settings=db.scalar(select(UserSetting).where(UserSetting.user_id==user.id))
        assert settings.provider_type!='ollama'
        uid=user.id
        project=Project(user_id=uid,name='summary-restart-'+uuid4().hex[:8])
        db.add(project);db.flush()
        pid=project.id
        conv=Conversation(user_id=uid,project_id=pid,title='摘要进程重启验收',model_name=settings.default_model,
            memory_extraction_cursor=300,memory_scan_cursor=300)
        db.add(conv);db.flush()
        cid=conv.id
        now=datetime.now(timezone.utc)-timedelta(hours=1)
        db.execute(insert(Message),[dict(id=str(uuid4()),conversation_id=cid,sequence=i+1,
            role='user' if i%2==0 else 'assistant',content=f'第{i+1}条工作记录，验收编号海鸥731。',status='done',
            created_at=now+timedelta(seconds=i)) for i in range(160)])
        job=ConversationSummaryService(db).ensure_job(MessageRepository(db).lock_conversation(cid))
        db.commit();jid=job.id
    try:
        deadline=time.monotonic()+120
        before=None
        while time.monotonic()<deadline:
            with SessionLocal() as db:
                job=db.get(ConversationSummaryJob,jid)
                if job.batches>=1:
                    before=(job.batches,job.cursor_message_id,job.lease_version,job.status)
                    break
                assert job.status!='failed',job.error_code
            time.sleep(.1)
        assert before and before[3]!='succeeded','未捕获尚未完成的已提交批次'
        control('restart')
        deadline=time.monotonic()+180
        after=None
        while time.monotonic()<deadline:
            with SessionLocal() as db:
                job=db.get(ConversationSummaryJob,jid)
                assert job.batches>=before[0],'重启丢失已提交进度'
                if job.status in {'succeeded','failed'}:
                    after=(job.status,job.batches,job.lease_version,job.error_code)
                    break
            time.sleep(.2)
        assert after and after[0]=='succeeded',after
        assert after[2]>before[2]
        print(json.dumps({'real_worker_restart':True,'same_job':True,'committed_batches_before':before[0],
            'batches_after':after[1],'lease_version_advanced':True,'status':after[0]},ensure_ascii=False))
    finally:
        cleanup(user_id=uid,project_id=pid)


if __name__=='__main__':
    restart_smoke() if os.getenv('AIWS_SUMMARY_RESTART_SMOKE')=='1' else run()
