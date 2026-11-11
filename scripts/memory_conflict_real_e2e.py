"""真实在线记忆关系、后台提取、浏览器审核与后续Chat；只创建合成临时资料。"""

import json
from chat_sse_client import chat_stream, iter_sse_events
import os
import time
from pathlib import Path
from uuid import uuid4

import httpx
from playwright.sync_api import sync_playwright, expect
from sqlalchemy import delete, select, update

from stage46a_real_e2e import prepare_fixture, cleanup, ROOT
from app.core.database import SessionLocal
from app.models import Conversation, Message, UserMemory, MemoryExtractionJob, UserSetting

API = os.getenv('AIWS_E2E_BACKEND_URL', 'http://127.0.0.1:32007')
WEB = os.getenv('AIWS_E2E_BASE_URL', 'http://127.0.0.1:32008')


def run():
    projects, cases, errors = [], [], []
    failure, candidate_diagnostics = None, None
    token = user_id = None
    client = None
    try:
        for name, old_content, new_content, allowed in (
            ('complement', '项目后端使用 FastAPI', '项目缓存使用 Redis', {'complement', 'different_scope'}),
            ('different_scope', '测试环境数据库使用 SQLite', '生产环境数据库使用 PostgreSQL', {'different_scope'}),
            ('duplicate', '项目资料默认保存30天', '本项目文档的默认保留期限为30天', {'duplicate'}),
        ):
            token, user_id, project_id, _, _ = prepare_fixture()
            projects.append(project_id)
            if client is None:
                client = httpx.Client(base_url=API, headers={'Authorization': f'Bearer {token}'}, timeout=120)
            def request(method, path, payload=None, expected=None):
                response = client.request(method, path, json=payload)
                assert response.status_code == expected if expected else response.is_success, f'API {path}: {response.status_code}'
                return response.json()
            old = request('POST', '/api/memories', dict(project_id=project_id, memory_type='project',
                title='技术栈' if name != 'duplicate' else '资料保留', content=old_content))
            assert old['status'] == 'active', '空项目的首条普通事实没有保存'
            new = request('POST', '/api/memories', dict(project_id=project_id, memory_type='project',
                title='技术栈' if name != 'duplicate' else '文档保存', content=new_content))
            pairs = new['conflict_review']['pairs']
            assert len(pairs) == 1 and pairs[0]['relation'] in allowed, f'{name}: 关系识别不符'
            assert pairs[0]['confidence'] >= .85, f'{name}: 模型低置信，仅验证到待审'
            if name == 'duplicate':
                assert new['status'] == 'pending'
                request('POST', f"/api/memories/{new['id']}/approve", {'expected_version': new['version'], 'acknowledge_conflicts': True}, 409)
            elif name == 'different_scope':
                assert new['status'] == 'pending', '高影响条件被自动启用'
                request('POST', f"/api/memories/{new['id']}/approve", {'expected_version': new['version']}, 409)
                new = request('POST', f"/api/memories/{new['id']}/approve", {'expected_version': new['version'], 'acknowledge_conflicts': True})
                assert new['status'] == 'active'
            else:
                assert new['status'] == 'active'
            with SessionLocal() as db:
                assert db.get(UserMemory, old['id']).status == 'active'
            row = dict(case=name, relation=pairs[0]['relation'], confidence=pairs[0]['confidence'], status='passed')
            cases.append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)

        token, user_id, project_id, _, _ = prepare_fixture()
        projects.append(project_id)
        suffix = uuid4().hex[:8]
        old_title = f'验收旧保留期-{suffix}'
        old = request('POST', '/api/memories', dict(project_id=project_id, memory_type='project',
            title=old_title, content='项目资料默认保留7天。'))
        with SessionLocal() as db:
            setting = db.scalar(select(UserSetting).where(UserSetting.user_id == user_id))
            assert setting.default_model == 'deepseek-ai/DeepSeek-V3.2' and setting.memory_enabled
            assert setting.provider_type == 'openai-compatible' and setting.knowledge_embedding_provider != 'ollama'
            conversation = Conversation(user_id=user_id, project_id=project_id, title='真实冲突提取验收', model_name=setting.default_model)
            db.add(conversation)
            db.flush()
            db.add_all([Message(conversation_id=conversation.id, role='user', sequence=1,
                                content='请记住：项目资料默认保留30天。'),
                        Message(conversation_id=conversation.id, role='assistant', sequence=2, content='收到。', status='done')])
            db.commit()
            conversation_id = conversation.id
        job = request('POST', f'/api/memories/extraction-jobs/{conversation_id}')
        started = time.monotonic()
        while time.monotonic() - started < 140:
            with SessionLocal() as db:
                current = db.get(MemoryExtractionJob, job['id'])
                state = current.status
                rows = list(db.scalars(select(UserMemory).where(UserMemory.extraction_job_id == job['id'])))
                candidate_ids = [row.id for row in rows]
            if state in {'succeeded', 'failed'}:
                break
            time.sleep(1)
        assert state == 'succeeded' and len(candidate_ids) == 1, '真实Worker没有完成一条候选提取'
        memories = request('GET', '/api/memories')
        candidate = next(row for row in memories if row['id'] == candidate_ids[0])
        review = candidate.get('conflict_review') or {}
        candidate_diagnostics = dict(status=candidate.get('status'), risk_level=candidate.get('risk_level'),
            coverage_complete=review.get('coverage_complete'), diagnostics=review.get('diagnostics'),
            pairs=[{key: pair.get(key) for key in ('relation', 'confidence', 'decision_source', 'impact')}
                   for pair in review.get('pairs', [])])
        assert candidate['status'] == 'pending' and candidate['risk_level'] == 'conflict'
        pair = candidate['conflict_review']['pairs'][0]
        assert pair['relation'] == 'contradiction' and pair['memory_id'] == old['id']
        request('POST', f"/api/memories/{candidate['id']}/approve", {'expected_version': candidate['version']}, 409)
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                context = browser.new_context(viewport={'width':1440,'height':1000})
                context.add_cookies([{'name':'aiws_token','value':token,'url':WEB,'httpOnly':True,'sameSite':'Lax'}])
                page = context.new_page()
                page.on('pageerror', lambda e: errors.append(type(e).__name__))
                page.on('console', lambda e: errors.append('console_error') if e.type == 'error' else None)
                page.goto(WEB + '/settings?tab=memory', wait_until='networkidle', timeout=60000)
                review = page.get_by_test_id('memory-conflict-review').filter(has_text=old_title)
                expect(review).to_be_visible()
                approve = review.get_by_role('button', name='确认替换并启用', exact=True)
                expect(approve).to_be_disabled()
                assert review.get_by_label('替换目标记忆').input_value() == old['id']
                review.screenshot(path='/tmp/aiws-memory-conflict-review.png')
                review.get_by_role('checkbox').check()
                expect(approve).to_be_enabled()
                approve.click()
                expect(review).not_to_be_visible(timeout=30000)
                page.reload(wait_until='networkidle')
                page.set_viewport_size({'width':390,'height':844})
                assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
                assert not errors, errors
            finally:
                browser.close()
        with SessionLocal() as db:
            previous, current = db.get(UserMemory, old['id']), db.get(UserMemory, candidate['id'])
            assert previous.status == 'superseded' and previous.version == 2
            assert current.status == 'active' and current.supersedes_memory_id == old['id']
            resolution = json.loads(current.conflict_review_json)['human_resolution']
            assert resolution['acknowledged'] and resolution['replacement_id'] == old['id'] and resolution['target_version'] == 1
        # 同一确认重复提交保持幂等，不再次改变旧行版本。
        request('POST', f"/api/memories/{candidate['id']}/approve", dict(expected_version=candidate['version'],
            supersedes_memory_id=old['id'], expected_target_version=1, acknowledge_conflicts=True))
        later = request('POST', '/api/conversations', {'project_id':project_id,'title':'新会话复核记忆',
                                                      'model_name':'deepseek-ai/DeepSeek-V3.2'})
        answer, injected, done = [], [], False
        with chat_stream(client, '/api/chat/runs', json={'conversation_id':later['id'],
            'content':'根据项目记忆，项目资料默认保留几天？仅回答天数。', 'model_name':'deepseek-ai/DeepSeek-V3.2'}) as response:
            assert response.is_success
            for event in iter_sse_events(response.iter_lines()):
                assert event['type'] != 'error', '后续Chat生成失败'
                if event['type'] == 'answer_delta':
                    answer.append(event['text'])
                elif event['type'] == 'context_info':
                    injected = event['info']['details']['memory_retrieval']['injected']
                elif event['type'] == 'done':
                    done = True
        assert done and '30' in ''.join(answer)
        assert candidate['id'] in {row['id'] for row in injected} and old['id'] not in {row['id'] for row in injected}
        cases.append(dict(case='worker_browser_replacement_and_next_chat', status='passed', relation='contradiction',
                          job_status=state, browser_errors=len(errors), next_answer=''.join(answer)))
        print(json.dumps(cases[-1], ensure_ascii=False), flush=True)
    except Exception as exc:
        failure = dict(error_type=type(exc).__name__, candidate_diagnostics=candidate_diagnostics)
        raise
    finally:
        if client:
            client.close()
        if user_id:
            for project_id in projects:
                with SessionLocal() as db:
                    db.execute(delete(MemoryExtractionJob).where(MemoryExtractionJob.user_id == user_id,
                        MemoryExtractionJob.project_id == project_id))
                    db.execute(update(UserMemory).where(UserMemory.user_id == user_id, UserMemory.project_id == project_id)
                               .values(supersedes_memory_id=None))
                    db.execute(delete(UserMemory).where(UserMemory.user_id == user_id, UserMemory.project_id == project_id))
                    db.commit()
                cleanup(user_id=user_id, project_id=project_id)
        # 指定新报告路径时保留失败分类；不保存候选原文、Token或真实账号其他资料。
        if failure and os.getenv('AIWS_MEMORY_CONFLICT_OUTPUT'):
            Path(os.environ['AIWS_MEMORY_CONFLICT_OUTPUT']).write_text(json.dumps(dict(
                cases=cases, failure=failure, temporary_data_cleaned=True), ensure_ascii=False, indent=2) + '\n')
    report = dict(model='deepseek-ai/DeepSeek-V3.2', embedding='BAAI/bge-m3', cases=cases,
                  passed=len(cases), temporary_data_cleaned=True, user_settings_unchanged=True)
    output = Path(os.getenv('AIWS_MEMORY_CONFLICT_OUTPUT', str(ROOT/'backend/evals/results/memory_conflicts_real_20261103.json')))
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'passed':len(cases),'temporary_data_cleaned':True}), flush=True)


if __name__ == '__main__':
    run()
