"""真实namespace与seccomp测试；不在宿主执行任何攻击样本。"""
import unittest
import time
from unittest.mock import patch
import subprocess
from app.services.sandbox_policy import SandboxPolicyError,script_digest
from sandbox.bwrap_runner import BubblewrapSandbox
from sandbox.runner import SandboxUnavailable


class BwrapPolicyTest(unittest.TestCase):
    def test_network_dependencies_and_changed_script_rejected_before_spawn(self):
        for values in ({'domains':['example.com']},{'dependencies':['x==1']},{'script_digest':'changed'}):
            plan={'script':'document_inventory','script_digest':script_digest('document_inventory'),'files':[],**values}
            with self.assertRaises(SandboxPolicyError):BubblewrapSandbox().execute(plan)


class BwrapRealTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.runtime=BubblewrapSandbox()
        try:cls.runtime.preflight()
        except (SandboxUnavailable,OSError) as exc:raise unittest.SkipTest('环境不支持真实namespace/seccomp') from exc

    def test_real_inventory_and_unicode_output(self):
        result=self.runtime.execute({'script':'document_inventory','script_digest':script_digest('document_inventory'),
            'files':[{'id':'f1','name':'中文资料','text':'你好\n世界'}]})
        self.assertEqual(len(result['artifacts']),2)
        self.assertIn('中文资料',result['artifacts'][0]['content'])

    def test_network_process_exec_and_limit_changes_denied(self):
        code=b'''import os,socket,subprocess,resource,json
result=[]
for f in (os.fork,lambda:socket.socket(),lambda:os.execv('/usr/bin/true',['true']),lambda:resource.setrlimit(resource.RLIMIT_CORE,(1,1))):
 try:f();result.append(False)
 except (OSError,ValueError):result.append(True)
print(json.dumps(result))
'''
        self.assertEqual(self.runtime._run_source(code,[]).strip(),b'[true, true, true, true]')

    def test_host_files_environment_and_writes_hidden(self):
        code=b'''import os,json
assert not os.path.exists('/home') and not os.path.exists('/etc/shadow')
assert set(os.environ)<= {'PATH','LC_ALL','PWD','LC_CTYPE','AIWS_SANDBOX_TRANSPORT'}
try:open('/input/manifest.json','w');raise AssertionError('write allowed')
except OSError:pass
print('ok')
'''
        self.assertEqual(self.runtime._run_source(code,[]).strip(),b'ok')

    def test_output_timeout_memory_and_cancel_are_bounded(self):
        with self.assertRaises(SandboxUnavailable):self.runtime._run_source(b"while True: print('x'*1000,flush=True)",[],max_bytes=10000)
        with self.assertRaises(SandboxUnavailable):self.runtime._run_source(b'while True: pass',[],seconds=.2)
        with self.assertRaises(SandboxUnavailable):self.runtime._run_source(b"data=bytearray(512*1024*1024)",[])
        deadline=time.monotonic()+.2
        processes=[]
        original=subprocess.Popen
        def capture(*args,**kwargs):
            p=original(*args,**kwargs);processes.append(p);return p
        with patch('sandbox.bwrap_runner.subprocess.Popen',side_effect=capture):
            with self.assertRaises(SandboxUnavailable):
                self.runtime._run_source(b'while True: pass',[],cancelled=lambda:time.monotonic()>deadline)
        self.assertTrue(all(p.poll() is not None for p in processes))
