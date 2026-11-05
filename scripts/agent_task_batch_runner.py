"""业务评测的真实执行适配层；不替代业务服务，不修改用户模型配置。"""

import json
from pathlib import Path
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from uuid import uuid4

import httpx
from sqlalchemy import delete, select

from app.core.config import settings
from app.core.database import SessionLocal
from app.models import Conversation, Message, Project, ProjectFile, User, UserSetting
from app.models.agent_runtime import (AgentApproval, AgentArtifact, AgentCheckpoint, AgentOutboxEvent,
                                      AgentRun, AgentStep, FileRevision, PatchDraft)
from app.models.knowledge import KnowledgeBase, KnowledgeChunk, KnowledgeDocument, KnowledgeRetrievalLog
from app.models.observability import ChatRuntimeMetric
from app.models.tool_trace import ToolCallRun, ToolRouteRun
from app.models.tool_config import WorkspaceAgentPolicy, WorkspaceToolSetting
from app.models.user_memory import MemoryExtractionJob, UserMemory
from app.repositories.user_repo import UserRepository
from app.services.auth_service import AuthService
from app.services.agent_task_evaluation import (check_answer, check_chat_evidence, classify, inspect_download,
                                                 inspect_trace, report, render_html)
from app.services.tools.schemas import redact_sensitive_arguments

ROOT = Path(__file__).resolve().parents[1]
MODEL = 'deepseek-ai/DeepSeek-V3.2'
TERMINAL = {'succeeded', 'failed', 'cancelled', 'dead_letter'}


class PrerequisiteMissing(ValueError):
    """未运行与运行失败分开计数。"""


def credentials(email):
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == email))
        if not user:
            raise PrerequisiteMissing('固定测试账号不存在')
        cfg = db.scalar(select(UserSetting).where(UserSetting.user_id == user.id))
        if not cfg or cfg.provider_type != 'openai-compatible' or cfg.default_model != MODEL:
            raise PrerequisiteMissing('要求账号现有配置为在线DeepSeek-V3.2；执行器不会自动修改设置')
        return user.id, AuthService(UserRepository(db)).create_access_token(user)


def json_response(response):
    # HTTP错误可能含远端地址或用户配置，只保存状态，不回显响应正文。
    if response.is_error:
        raise RuntimeError(f'业务接口HTTP {response.status_code}')
    return response.json()


def knowledge_target(case, user_id):
    """读取固定Gold，不重标答案，也不为了通过切换资料版本。"""
    if not case['expected'].get('knowledge_required'):
        return None
    gold_path = (ROOT / 'backend/evals' / case['gold_file']).resolve()
    if gold_path.parent != ROOT / 'backend/evals' or not gold_path.is_file():
        raise PrerequisiteMissing('Gold文件缺失或路径越界')
    gold = json.loads(gold_path.read_text())
    target = next((item for item in gold['cases'] if item['case_id'] == case['gold_case']), None)
    if target is None:
        raise PrerequisiteMissing('Gold题号不存在')
    with SessionLocal() as db:
        kb = db.scalar(select(KnowledgeBase).where(KnowledgeBase.id == gold['knowledge_base_id'],
                                                 KnowledgeBase.user_id == user_id))
        if not kb or kb.active_index_generation != gold['index_generation']:
            raise PrerequisiteMissing('知识库不存在、无权访问或活动索引版本变化')
        chunks = []
        for selector in target['expected_chunk_selectors']:
            chunk = db.scalar(select(KnowledgeChunk).join(KnowledgeDocument).where(
                KnowledgeChunk.knowledge_base_id == kb.id, KnowledgeChunk.user_id == user_id,
                KnowledgeChunk.index_generation == kb.active_index_generation,
                KnowledgeDocument.file_name == selector['document_file'],
                KnowledgeChunk.chunk_index == selector['chunk_index']))
            normalize = lambda value: re.sub(r'\s+', '', value)
            if not chunk or normalize(selector['contains']) not in normalize(chunk.content):
                raise PrerequisiteMissing('Gold原文定位失效，不能悄悄换Chunk')
            chunks.append(chunk.id)
        return {'id': kb.id, 'generation': kb.active_index_generation, 'gold_chunk_ids': chunks,
                'gold_case': case['gold_case'], 'retrieval_mode': kb.retrieval_mode,
                'retrieval_top_k': kb.retrieval_top_k, 'rerank_top_n': kb.rerank_top_n,
                'rerank_enabled': kb.rerank_enabled}


def input_names(case):
    if case.get('files'):
        return case['files']
    if case['expected'].get('no_tools'):
        return []
    if case['expected'].get('read_files'):
        return case['expected']['read_files']
    return list(dict.fromkeys(re.findall(r'\$\{file:([^}]+)\}', case['query'])))


def unchanged_baselines(revisions, fixture_by_file):
    """读取可补建版本1基线，但不能产生修改版本或关联写入Run。"""
    seen = set()
    for revision in revisions:
        if (revision.project_file_id in seen or revision.revision_number != 1 or revision.created_by != 'baseline'
            or revision.source_run_id or revision.source_step_id
            or revision.parsed_text != fixture_by_file.get(revision.project_file_id)):
            return False
        seen.add(revision.project_file_id)
    return True


class PilotRunner:
    def __init__(self, client, user_id, dataset, artifact_dir, timeout):
        self.client, self.user_id, self.dataset = client, user_id, dataset
        self.artifact_dir, self.timeout = artifact_dir, timeout
        self.projects = set()
        self.task_ids = {}

    def create_project(self, case):
        project = json_response(self.client.post('/api/projects', json={
            'name': f"agent-pilot-{case['id']}-{uuid4().hex[:8]}", 'default_model': MODEL,
            'description': '合成资料批量验收，运行结束清理；不是实际客户项目'}))
        self.projects.add(project['id'])
        return project['id']

    def prepare_files(self, case, project_id):
        file_ids = {}
        for name in input_names(case):
            if name not in self.dataset['fixtures'] or Path(name).name != name or '\\' in name:
                raise ValueError('题本资料名不合法')
            content = self.dataset['fixtures'][name]
            key = f'{self.user_id}/agent_evals/{project_id}/{name}'
            path = Path(settings.upload_dir) / key
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content.encode())
            file = json_response(self.client.post(f'/api/projects/{project_id}/files', json={
                'file_name': name, 'kind': 'text', 'mime_type': 'text/plain',
                'file_size': len(content.encode()), 'storage_key': key, 'parsed_text': content}))
            file_ids[name] = file['id']
        return file_ids

    def safety_checks(self, project_id, file_ids):
        """核对数据库正文和真实文件；done不能证明没有越权写入。"""
        with SessionLocal() as db:
            files = list(db.scalars(select(ProjectFile).where(ProjectFile.project_id == project_id)))
            same = {file.id for file in files} == set(file_ids.values())
            for file in files:
                content = self.dataset['fixtures'].get(file.file_name)
                if content is None:
                    same = False
                    continue
                path = Path(settings.upload_dir) / file.storage_key
                same = same and file.parsed_text == content and path.is_file() and path.read_bytes() == content.encode()
            runs = list(db.scalars(select(AgentRun.id).where(AgentRun.project_id == project_id)))
            approvals = db.scalar(select(AgentApproval.id).where(AgentApproval.run_id.in_(runs)).limit(1)) if runs else None
            revisions = list(db.scalars(select(FileRevision).where(FileRevision.project_file_id.in_(file_ids.values()))))
            baseline_only = unchanged_baselines(revisions, {file_id: self.dataset['fixtures'][name] for name, file_id in file_ids.items()})
            return {'source_files_unchanged': bool(same), 'no_unrequested_approval': approvals is None,
                    'no_unrequested_revision': baseline_only}

    def conversation(self, case, project_id):
        return json_response(self.client.post('/api/conversations', json={
            'title': '先行评测 ' + case['id'], 'project_id': project_id, 'model_name': MODEL}))['id']

    def chat_snapshot(self, conversation_id):
        """即使流失败也读取已持久化的事实，不能在清理后再猜失败原因。"""
        with SessionLocal() as db:
            message = db.scalar(select(Message).where(Message.conversation_id == conversation_id,
                                Message.role == 'assistant').order_by(Message.sequence.desc()))
            routes = list(db.scalars(select(ToolRouteRun).where(ToolRouteRun.user_id == self.user_id,
                                     ToolRouteRun.conversation_id == conversation_id).order_by(ToolRouteRun.created_at)))
            calls = list(db.scalars(select(ToolCallRun).where(ToolCallRun.route_run_id.in_([r.id for r in routes]))
                                    .order_by(ToolCallRun.created_at)))
            metric = db.scalar(select(ChatRuntimeMetric).where(ChatRuntimeMetric.conversation_id == conversation_id))
            return {
                'assistant_message_id': message.id if message else None,
                'assistant_status': message.status if message else None,
                'answer': message.content if message else '',
                'source_map': json.loads(message.external_sources or '[]') if message else [],
                'citation_audit': json.loads(message.citation_audit or '{}') if message else {},
                'events': [event for route in routes for event in json.loads(route.events_json or '[]')],
                'routes': [{'id': r.id, 'status': r.status, 'elapsed_ms': r.elapsed_ms,
                            'plan': json.loads(r.plan_json or '{}')} for r in routes],
                'calls': [{'call_id': c.call_id, 'tool_key': c.tool_key, 'status': c.status,
                           'arguments': redact_sensitive_arguments(json.loads(c.arguments_json or '{}')),
                           'elapsed_ms': c.elapsed_ms, 'error_message': c.error_message} for c in calls],
                'runtime_stats': json.loads(metric.stats_json or '{}') if metric else {},
            }

    def request_chat(self, case, file_ids, conversation_id, knowledge=None):
        query = case['query']
        for name, file_id in file_ids.items():
            query = query.replace('${file:' + name + '}', file_id)
        if '${file:' in query:
            raise ValueError('输入资料未绑定')
        stream_events, error_type, usage_count, final_usage, http_status = [], None, 0, None, None
        deadline = time.monotonic() + self.timeout
        try:
            with self.client.stream('POST', '/api/chat/events-stream', json={
                'conversation_id': conversation_id, 'content': query, 'model_name': MODEL,
                'web_search_enabled': True, 'tool_run_mode': case['mode'],
                'knowledge_base_ids': [knowledge['id']] if knowledge else [],
            }) as response:
                http_status=response.status_code
                if response.is_error:
                    raise RuntimeError(f'Chat接口HTTP {response.status_code}')
                for line in response.iter_lines():
                    if time.monotonic() > deadline:
                        raise RuntimeError('Chat超过总时长上限')
                    if line.strip():
                        event = json.loads(line)
                        # 增量正文以数据库最终消息为准，避免报告重复几千段Token。
                        if event.get('type') == 'provider_usage':
                            usage_count, final_usage = usage_count + 1, event
                        elif event.get('type') not in {'answer_delta', 'reasoning_delta'}:
                            stream_events.append(event)
        except (httpx.HTTPError, ValueError, RuntimeError) as exc:
            error_type = type(exc).__name__
        row = self.chat_snapshot(conversation_id)
        row.update(stream_events=stream_events, provider_usage_events=usage_count, final_provider_usage=final_usage,
                   error_type=error_type, knowledge_target=knowledge, http_status=http_status)
        row['final_answer_input_tokens'] = row['runtime_stats'].get('provider_input_tokens')
        row['final_answer_output_tokens'] = row['runtime_stats'].get('provider_output_tokens')
        return row

    def run_chat(self, case, project_id, file_ids, conversation_id, knowledge):
        row = self.request_chat(case, file_ids, conversation_id, knowledge)
        expected, calls = case['expected'], row['calls']
        row['trace'] = inspect_trace(row['events'], calls, expected.get('required_tool_groups', []),
                                     expected.get('forbidden_tool_keys', []))
        checks = check_answer(row['answer'], expected)
        checks.update(check_chat_evidence(row['answer'], row['source_map'], expected, file_ids,
                                         knowledge['gold_chunk_ids'] if knowledge else []))
        checks['stream_and_database_done'] = (not row['error_type'] and row['assistant_status'] == 'done'
            and any(e.get('type') == 'done' for e in row['stream_events'])
            and not any(e.get('type') == 'error' for e in row['stream_events']))
        if expected.get('no_tools'):
            checks['no_tool_calls'] = not calls and not row['trace']['planned_keys']
        if 'max_tool_calls' in expected:
            checks['call_count_within_limit'] = len(calls) <= expected['max_tool_calls']
        succeeded = {c['tool_key'] for c in calls if c['status'] == 'success'}
        for index, group in enumerate(expected.get('required_tool_groups', [])):
            checks[f'required_group_executed_{index + 1}'] = bool(
                ({c['tool_key'] for c in calls} if expected.get('expected_read_refusal') else succeeded).intersection(group))
        # 本批所有题均禁止联网/脚本/写入。未知工具也不能凭成功状态获得豁免。
        allowed = set(expected.get('allowed_tool_keys', ['workspace.files.read', 'workspace.files.search', 'workspace.files.list']))
        checks['only_allowed_tool_calls'] = all(c['tool_key'] in allowed for c in calls)
        if case['category'] == 'single_tool' and expected.get('required_tool_groups') == [['workspace.files.read']]:
            checks['only_direct_file_read'] = all(c['tool_key'] == 'workspace.files.read' for c in calls)
            checks['correct_file_argument'] = all(c['arguments'].get('file_id') in file_ids.values() for c in calls)
        checks.update(self.safety_checks(project_id, file_ids))
        row['checks'] = checks
        row['critical_checks'] = ['only_allowed_tool_calls', 'source_files_unchanged',
                                 'no_unrequested_approval', 'no_unrequested_revision', 'forbidden_claims_absent']
        row['status'] = classify(checks, row['critical_checks'])
        return row

    def run_document(self, case, project_id, file_ids, conversation_id):
        task = json_response(self.client.post('/api/document-tasks', json={
            'project_id': project_id, 'conversation_id': conversation_id, 'file_ids': list(file_ids.values()),
            'instruction': case['query'], 'file_name': case['id'] + '.' + ('md' if case['format'] == 'markdown' else 'docx'),
            'format': case['format'], 'idempotency_key': 'agent-pilot-' + uuid4().hex}))
        run_id, deadline = task['id'], time.monotonic() + self.timeout
        self.task_ids[case['id']] = run_id
        while task['status'] not in TERMINAL and time.monotonic() < deadline:
            time.sleep(1)
            task = json_response(self.client.get(f'/api/document-tasks/{run_id}'))
        timed_out = task['status'] not in TERMINAL
        if timed_out:
            json_response(self.client.post(f'/api/document-tasks/{run_id}/cancel'))
            task = json_response(self.client.get(f'/api/document-tasks/{run_id}'))
        checks = {'task_succeeded': task['status'] == 'succeeded' and not timed_out,
                  'no_unrequested_approval': not task['approvals'],
                  'download_does_not_save_workspace': task['workspace_save_status'] == 'planned'}
        stages = {step['stage']: step['status'] for step in task['steps']}
        checks['generation_stages_succeeded'] = all(stages.get(key) == 'succeeded' for key in ('evidence', 'draft', 'export'))
        exports = [a for a in task['artifacts'] if a['stage'] == 'document_export']
        checks['exactly_one_export'] = len(exports) == 1
        row = {'run_id': run_id, 'task_snapshot': task, 'answer': '', 'downloads': [],
               'error_type': 'TaskDeadlineExceeded' if timed_out else None}
        for artifact in exports:
            detail = json_response(self.client.get(f'/api/document-tasks/{run_id}/artifacts/{artifact["id"]}'))
            response = self.client.get(f'/api/document-tasks/{run_id}/artifacts/{artifact["id"]}/download')
            response.raise_for_status()
            path = self.artifact_dir / f'{case["id"]}.{case["format"] if case["format"] != "markdown" else "md"}'
            path.write_bytes(response.content)
            try:
                parsed = inspect_download(response.content, case['format'])
                row['answer'] = parsed['body']
                checks.update(check_answer(parsed['body'], case['expected']))
                checks['download_valid'] = parsed['format_valid']
                refs = detail['content'].get('references', [])
                checks['references_match_inputs'] = all(
                    any(file_id in ref and name in ref for ref in refs) and name in parsed['text']
                    for name, file_id in file_ids.items())
                row['downloads'].append({'path': 'artifacts/' + path.name, 'bytes': parsed['bytes'],
                                         'references': refs, 'artifact_id': artifact['id']})
            except (ValueError, KeyError, UnicodeError) as exc:
                checks['download_valid'] = False
                row['error_type'] = type(exc).__name__
        if not exports:
            checks['download_valid'] = False
            checks.update(check_answer('', case['expected']))
        with SessionLocal() as db:
            messages = list(db.scalars(select(Message).where(Message.conversation_id == conversation_id,
                                                           Message.role == 'assistant')))
            projected = [m for m in messages if any((s.get('metadata') or {}).get('run_id') == run_id
                                                  for s in json.loads(m.external_sources or '[]'))]
            checks['one_result_message_in_original_conversation'] = (len(projected) == 1
                and projected[0].id == task.get('result_message_id') and projected[0].status == 'done'
                and '/documents?run=' + run_id in projected[0].content)
            row['result_messages'] = [{'id': m.id, 'status': m.status, 'content': m.content} for m in projected]
            row['checkpoints'] = [{'step_sequence': c.step_sequence, 'state_version': c.state_version}
                                 for c in db.scalars(select(AgentCheckpoint).where(AgentCheckpoint.run_id == run_id))]
        checks.update(self.safety_checks(project_id, file_ids))
        row['checks'] = checks
        row['critical_checks'] = ['source_files_unchanged', 'no_unrequested_approval', 'no_unrequested_revision',
                                 'download_does_not_save_workspace', 'forbidden_claims_absent']
        # 没有产物时禁用声明检查仍可通过，但整体不得判作已完成。
        row['status'] = classify(checks, row['critical_checks'])
        return row

    def run_case(self, case):
        started = time.monotonic()
        row = {key: case[key] for key in ('id', 'category', 'business', 'runner')}
        row.update(query=case['query'], model=MODEL)
        project_id, files = None, {}
        try:
            knowledge = knowledge_target(case, self.user_id)
            project_id = self.create_project(case)
            row['project_id'] = project_id
            files = self.prepare_files(case, project_id)
            conversation_id = self.conversation(case, project_id)
            row.update(conversation_id=conversation_id, input_files=files)
            task_started = time.monotonic()
            row['provisioning_ms'] = round((task_started - started) * 1000, 1)
            if case['runner'] == 'chat':
                row.update(self.run_chat(case, project_id, files, conversation_id, knowledge))
            elif case['runner'] == 'document_task':
                row.update(self.run_document(case, project_id, files, conversation_id))
            elif case['runner'] == 'file_approval':
                from agent_task_approval_runner import run_approval
                row.update(run_approval(self, case, project_id, files, conversation_id))
            elif case['runner'] == 'fault_injection':
                from agent_task_fault_runner import run_probe
                row.update(run_probe(self, case, project_id, files, conversation_id))
            else:
                raise PrerequisiteMissing('受控故障注入尚未接入本批执行器')
            row['task_elapsed_ms'] = round((time.monotonic() - task_started) * 1000, 1)
        except PrerequisiteMissing as exc:
            row.update(status='skipped', error_type=type(exc).__name__, skip_reason=str(exc))
        except Exception as exc:
            # 逐题继续；错误类型可公开，原始异常可能带凭据，不能直接str(exc)。
            row.update(status='failed', error_type=type(exc).__name__)
            if row.get('conversation_id') and case['runner'] in {'chat','file_approval'}:
                try:
                    row.update(self.chat_snapshot(row['conversation_id']))
                except Exception as snapshot_error:
                    row['snapshot_error_type'] = type(snapshot_error).__name__
                if case['runner']=='file_approval' and files:
                    from agent_task_approval_runner import snapshot
                    row['approval_state_after_error']=snapshot(next(iter(files.values())))
            if project_id:
                try:
                    row['safety_after_failure'] = self.safety_checks(project_id, files)
                except Exception as safety_error:
                    row['safety_error_type'] = type(safety_error).__name__
            if case['runner'] == 'document_task' and case['id'] in self.task_ids:
                row['run_id'] = self.task_ids[case['id']]
                try:
                    row['task_snapshot'] = json_response(self.client.get('/api/document-tasks/' + row['run_id']))
                except Exception as snapshot_error:
                    row['snapshot_error_type'] = type(snapshot_error).__name__
        row['elapsed_ms'] = round((time.monotonic() - started) * 1000, 1)
        return row

    def cleanup(self):
        """只清理执行器创建的项目；先结束活跃任务，再按外键顺序释放临时资料。"""
        for project_id in self.projects:
            with SessionLocal() as db:
                runs = list(db.scalars(select(AgentRun.id).where(AgentRun.project_id == project_id,
                    AgentRun.runtime_kind == 'document_workflow', AgentRun.status.not_in(TERMINAL))))
            for run_id in runs:
                json_response(self.client.post(f'/api/document-tasks/{run_id}/cancel'))
            with SessionLocal() as db:
                project = db.scalar(select(Project).where(Project.id == project_id, Project.user_id == self.user_id))
                if not project or not project.name.startswith('agent-pilot-'):
                    raise ValueError('清理范围不匹配')
                conversations = select(Conversation.id).where(Conversation.project_id == project_id)
                routes = select(ToolRouteRun.id).where(ToolRouteRun.conversation_id.in_(conversations))
                runs = select(AgentRun.id).where(AgentRun.project_id == project_id)
                files = select(ProjectFile.id).where(ProjectFile.project_id == project_id)
                db.execute(delete(ToolCallRun).where(ToolCallRun.route_run_id.in_(routes)))
                db.execute(delete(ToolRouteRun).where(ToolRouteRun.conversation_id.in_(conversations)))
                db.execute(delete(KnowledgeRetrievalLog).where(KnowledgeRetrievalLog.conversation_id.in_(conversations)))
                db.execute(delete(ChatRuntimeMetric).where(ChatRuntimeMetric.conversation_id.in_(conversations)))
                db.execute(delete(MemoryExtractionJob).where(MemoryExtractionJob.conversation_id.in_(conversations)))
                db.execute(delete(UserMemory).where(UserMemory.project_id == project_id))
                db.execute(delete(WorkspaceAgentPolicy).where(WorkspaceAgentPolicy.project_id == project_id))
                db.execute(delete(WorkspaceToolSetting).where(WorkspaceToolSetting.project_id == project_id))
                db.execute(delete(PatchDraft).where(PatchDraft.run_id.in_(runs)))
                db.execute(delete(FileRevision).where(FileRevision.project_file_id.in_(files)))
                for model in (AgentApproval, AgentArtifact, AgentCheckpoint, AgentOutboxEvent, AgentStep):
                    db.execute(delete(model).where(model.run_id.in_(runs)))
                db.execute(delete(AgentRun).where(AgentRun.project_id == project_id))
                db.execute(delete(ProjectFile).where(ProjectFile.project_id == project_id))
                db.execute(delete(Message).where(Message.conversation_id.in_(conversations)))
                db.execute(delete(Conversation).where(Conversation.project_id == project_id))
                db.execute(delete(Project).where(Project.id == project_id))
                db.commit()
            path = Path(settings.upload_dir) / self.user_id / 'agent_evals' / project_id
            if path.exists():
                shutil.rmtree(path)
        return {'status': 'cleaned', 'projects': len(self.projects)}


def execute_batch(dataset, *, base_url, email, output, html_path, timeout, browser_smoke=False, jobs=1):
    """每题落盘后再清理；中断/缺前提/失败都保留在完整题本分母与报告中。"""
    output.parent.mkdir(parents=True, exist_ok=True)
    artifacts = output.parent / 'artifacts'
    artifacts.mkdir(exist_ok=True)
    rows = [{**{key: case[key] for key in ('id', 'category', 'business', 'runner')},
             'status': 'skipped', 'skip_reason': 'not_run'} for case in dataset['cases']]
    metadata = {'model': MODEL, 'status': 'running', 'cleanup': {'status': 'not_started'},
                'dataset_snapshot': dataset,
                'started_at': datetime.now(timezone.utc).isoformat(),
                'git_commit': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                'worktree_modified': bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip())}

    def save():
        payload = report(dataset, rows)
        payload.update(metadata)
        payload = redact_sensitive_arguments(payload)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n')
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(render_html(payload), encoding='utf-8')
        return payload

    save()
    runner = None
    try:
        uid, token = credentials(email)
        with httpx.Client(base_url=base_url, headers={'Authorization': 'Bearer ' + token},
                          timeout=httpx.Timeout(timeout), trust_env=False) as client:
            runner = PilotRunner(client, uid, dataset, artifacts, timeout)
            health = client.get('/health/workers')
            metadata['worker_health'] = health.json()
            try:
                with ThreadPoolExecutor(max_workers=jobs) as pool:
                    futures = {pool.submit(runner.run_case, case): index for index,case in enumerate(dataset['cases'])}
                    for future in as_completed(futures):
                        index = futures[future]
                        case = dataset['cases'][index]
                        rows[index] = future.result()
                        if (browser_smoke and case['runner']=='document_task' and case.get('format')=='docx'
                            and rows[index].get('run_id') and rows[index]['status']=='passed' and not metadata.get('browser')):
                            save()
                            try:
                                metadata['browser'] = check_browser(rows[index]['run_id'], token, output.parent, html_path)
                            except Exception as exc:
                                metadata['browser'] = {'status': 'failed', 'error_type': type(exc).__name__}
                        save()
                        print(json.dumps({'case': case['id'], 'status': rows[index]['status'],
                                          'failed_checks': [key for key, value in rows[index].get('checks', {}).items() if not value],
                                          'error_type': rows[index].get('error_type')}, ensure_ascii=False), flush=True)
            finally:
                # 失败快照已先保存在本地，清理不会抹掉诊断。
                save()
                try:
                    metadata['cleanup'] = runner.cleanup()
                except Exception as exc:
                    metadata['cleanup'] = {'status': 'failed', 'error_type': type(exc).__name__}
    except PrerequisiteMissing as exc:
        metadata['prerequisite_error'] = str(exc)
    except Exception as exc:
        metadata['execution_error_type'] = type(exc).__name__
    finally:
        metadata['status'] = 'executed_results'
        metadata['finished_at'] = datetime.now(timezone.utc).isoformat()
        payload = save()
    return payload


def check_browser(run_id, token, output_dir, html_path):
    """从实际业务页面下载一次Word；不点击工作区保存，不追加审批。"""
    from playwright.sync_api import sync_playwright, expect

    base = 'http://127.0.0.1:32008'
    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={'width': 1440, 'height': 1000})
        context.add_cookies([{'name': 'aiws_token', 'value': token, 'url': base,
                             'httpOnly': True, 'sameSite': 'Lax'}])
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(type(error).__name__))
        page.on('console', lambda message: errors.append('console_error') if message.type == 'error' else None)
        page.goto(f'{base}/documents?run={run_id}', wait_until='networkidle')
        expect(page.get_by_role('heading', name='文档任务与产物', exact=True)).to_be_visible()
        expect(page.get_by_text('文档：已生成，可预览下载', exact=True)).to_be_visible()
        page.get_by_role('button', name='打开产物', exact=True).last.click()
        with page.expect_download() as download:
            page.get_by_role('link', name='下载导出文件', exact=True).click()
        path = output_dir / 'artifacts/browser-download.docx'
        download.value.save_as(path)
        inspect_download(path.read_bytes(), 'docx')
        page.screenshot(path=str(output_dir / 'browser-document.png'), full_page=True)
        page.goto(html_path.as_uri(), wait_until='load')
        expect(page.get_by_role('heading', name='Agent业务评测')).to_be_visible()
        screenshot = output_dir / 'browser-report.png'
        page.screenshot(path=str(screenshot), full_page=True)
        context.close()
        browser.close()
    return {'status': 'passed' if not errors else 'failed', 'errors': errors,
            'download': 'artifacts/browser-download.docx'}
