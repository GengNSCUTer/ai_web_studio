"""不同代码共用执行器，验证真实隔离与批准边界，不只检查HTTP格式。"""

import asyncio
import json
import unittest
from unittest.mock import patch

from app.schemas.plugins import SandboxCodePreviewCreate
from app.services.plugin_service import PluginError
from app.services.sandbox_service import SandboxService
from app.services.sandbox_policy import SandboxPolicyError, validate_code
from pydantic import ValidationError
from app.models.plugin import SandboxJob
from app.models.tool_config import WorkspaceAgentPolicy
from app.services.tools.catalog import ToolCatalog
from app.services.tools.executor import ToolExecutor
from app.services.tools.schemas import PlannedToolCall
from app.services.tools.providers.sandbox_python import SandboxPythonProvider
from backend.tests import test_plugin_center as fixtures
from sandbox.bwrap_runner import BubblewrapSandbox
from sandbox.runner import SandboxUnavailable


class PythonAuthorizationTest(unittest.TestCase):
    setUp = fixtures.PluginCenterTest.setUp
    tearDown = fixtures.PluginCenterTest.tearDown
    def create_code(self, **values):
        return SandboxService(self.db).preview_code('u', SandboxCodePreviewCreate(
            **{'project_id': 'p', 'code': 'print(42)', **values}))

    def test_code_requires_confirmation_and_cannot_be_replaced(self):
        preview = self.create_code(file_ids=['f'])
        self.assertEqual(preview['status'], 'awaiting_approval')
        row = self.db.get(SandboxJob, preview['id'])
        self.assertEqual(json.loads(row.plan_json)['code'], 'print(42)')
        with patch('app.services.sandbox_service.sandbox_availability', return_value={
                'available': True, 'backend': 'bubblewrap', 'python_available': True}):
            self.assertEqual(SandboxService(self.db).confirm('u', row.id)['status'], 'queued')
            self.assertEqual(SandboxService(self.db).confirm('u', row.id)['id'], row.id)
        with self.assertRaises(PluginError):
            SandboxService(self.db).confirm('other', row.id)
        call = PlannedToolCall(call_id='lookup', tool_key='sandbox.jobs.get', provider='sandbox',
            category='sandbox', display_name='结果', arguments={'job_id': row.id}, confidence=1, reason='test')
        with self.assertRaises(PluginError):
            asyncio.run(SandboxPythonProvider(db=self.db, user_id='u', project_id='q').run(call))

    def test_no_runtime_readonly_and_foreign_input_refused(self):
        for values in ({'project_id': 'x'}, {'file_ids': ['missing']}):
            with self.assertRaises(PluginError): self.create_code(**values)
        with self.assertRaises(SandboxPolicyError): self.create_code(code='x\x00')
        with self.assertRaises(ValidationError): self.create_code(code=chr(0xD800))
        with self.assertRaises(SandboxPolicyError): validate_code(chr(0xD800))
        preview = self.create_code()
        with patch('app.services.sandbox_service.sandbox_availability', return_value={
                'available': True, 'backend': 'bubblewrap', 'python_available': False}):
            with self.assertRaisesRegex(PluginError, '未就绪'):
                SandboxService(self.db).confirm('u', preview['id'])
        self.db.add(WorkspaceAgentPolicy(project_id='p', permission_mode='read_only'))
        self.db.commit()
        with self.assertRaises(PluginError): self.create_code()
        with self.assertRaises(PluginError): SandboxService(self.db).confirm('u', preview['id'])

    def test_executor_only_prepares_and_checks_original_intent(self):
        executor = ToolExecutor(db=self.db, user_id='u', project_id='p', catalog=ToolCatalog(),
            original_task='请用Python统计这组数字')
        call = PlannedToolCall(call_id='code1', tool_key='sandbox.python.execute', provider='sandbox',
            category='sandbox', display_name='Python', arguments={'code': 'print(42)'}, confidence=1, reason='test')
        result, events = asyncio.run(executor.execute(call))
        self.assertEqual(result.status, 'confirmation_required')
        event = next(e for e in events if e.type == 'sandbox_confirmation_required')
        self.assertEqual(self.db.get(SandboxJob, event.payload['job_id']).status, 'awaiting_approval')
        executor.action_guard.scope = __import__('app.services.tools.action_guard',fromlist=['ActionScope']).ActionScope('你好')
        result, _ = asyncio.run(executor.execute(call))
        self.assertNotEqual(result.status, 'success')


class PythonRealRuntimeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime = BubblewrapSandbox()
        try: cls.runtime.code_preflight()
        except (SandboxUnavailable, OSError) as exc:
            raise unittest.SkipTest('缺少独立Python3.12或namespace条件') from exc

    def execute(self, code, files=None):
        return self.runtime.execute({'script':'python_stdlib','code':code,'files':files or []})

    def test_multiple_businesses_use_same_runtime(self):
        cases = [
            ('import statistics;print(statistics.mean([2,4,6]))', '4'),
            ('import json;print(json.dumps(sorted(set([3,1,3]))))', '[1, 3]'),
            ('import csv,io;print(sum(int(r["n"]) for r in csv.DictReader(io.StringIO("n\\n2\\n5\\n"))))','7'),
            ('from collections import Counter;print(Counter(["好","差","好"])["好"])','2'),
            ('from decimal import Decimal;print(Decimal("0.1")+Decimal("0.2"))','0.3'),
            ('type Number = int\nprint("python312")','python312'),
            ('emit_artifact("report.md", "# 通用报告\\n统计完成");print("done")','done'),
        ]
        for code, expected in cases:
            with self.subTest(code=code): self.assertIn(expected, self.execute(code)['stdout'])

    def test_authorized_input_and_artifact(self):
        code = 'text=next(input_dir.glob("*.txt")).read_text();emit_artifact("result.txt",text.upper())'
        result = self.execute(code, [{'id':'f1','name':'a.txt','text':'hello'}])
        self.assertEqual(result['artifacts'][0]['content'], 'HELLO')

    def test_os_denies_operations_not_business_names(self):
        for code in ('if :', 'import socket;socket.socket()', 'import os;os.fork()',
                     'import os;os.execv("/bin/sh",["sh"])', 'open("/input/x","w")',
                     'open("/home/secret")', 'x=bytearray(512*1024*1024)'):
            with self.subTest(code=code), self.assertRaises(SandboxUnavailable): self.execute(code)
        with self.assertRaises(SandboxUnavailable): self.execute('while True: print("x"*10000)')
