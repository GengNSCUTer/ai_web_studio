"""原生Bubblewrap离线执行器；只运行审核脚本，不继承宿主环境或开放任意代码。"""
import json
import hashlib
import os
from pathlib import Path
import re
import selectors
import shutil
import signal
import stat
import subprocess
import tempfile
import time

from app.services.sandbox_policy import LIMITS, SCRIPTS, SandboxPolicyError
from sandbox.runner import SandboxUnavailable


class BubblewrapSandbox:
    def preflight(self):
        binary = shutil.which('bwrap', path='/usr/bin:/bin')
        if not binary or os.getuid()==0 or Path(binary).stat().st_mode & (stat.S_ISUID|stat.S_ISGID):
            raise SandboxUnavailable('需要普通非root账号和非setuid Bubblewrap。')
        # 必须实际加载seccomp并验证fork、网络、写入和限额修改被拒绝。
        output=self._run_source(b'''import json,os,socket,resource
checks=[]
for action in (lambda: os.fork(), lambda: socket.socket(), lambda: open('/forbidden','w'), lambda: resource.setrlimit(resource.RLIMIT_CORE,(1,1))):
 try: action(); checks.append(False)
 except (OSError,ValueError): checks.append(True)
print(json.dumps(checks))
''', [])
        if json.loads(output)!=[True]*4:
            raise SandboxUnavailable('Bubblewrap边界探测失败。')
        return 'bubblewrap/offline/single-process/seccomp/rlimit 就绪；无动态依赖'

    def resolve_dependencies(self, plan):
        raise SandboxPolicyError('Bubblewrap首版不支持联网或动态依赖，请重新预览离线任务。')

    def execute(self, plan, *, cancelled=None):
        if plan.get('domains') or plan.get('dependencies') or plan.get('wheel_lock'):
            raise SandboxPolicyError('Bubblewrap只支持无联网、无动态依赖的任务。')
        if plan.get('script') not in SCRIPTS:
            raise SandboxPolicyError('审核脚本已变化，请重新预览授权。')
        source=Path(__file__).parent/'scripts'/SCRIPTS[plan['script']]['file_name']
        code=source.read_bytes()
        if plan.get('script_digest')!=hashlib.sha256(code).hexdigest():
            raise SandboxPolicyError('审核脚本已变化，请重新预览授权。')
        raw=self._run_source(code,plan['files'],cancelled=cancelled)
        try:
            result=json.loads(raw)
            artifacts=result['artifacts']
            if not isinstance(artifacts,list) or len(artifacts)>16:
                raise ValueError()
            total=0
            for item in artifacts:
                if not re.fullmatch(r'[A-Za-z0-9_-]+\.(?:md|txt|json|csv)',item['name']):
                    raise ValueError()
                text=item['content']
                if not isinstance(text,str) or '\x00' in text:
                    raise ValueError()
                total+=len(text.encode())
            if total>LIMITS['artifact_bytes'] or len({x['name'] for x in artifacts})!=len(artifacts):
                raise ValueError()
            return {'artifacts':artifacts,'stdout':str(result.get('stdout',''))[:4096]}
        except (ValueError,KeyError,TypeError) as exc:
            raise SandboxPolicyError('隔离产物格式或大小不符合要求。') from exc

    def _run_source(self, source, files, *, cancelled=None, seconds=45, max_bytes=1024*1024):
        """仅供执行器和边界测试调用；不是Web/Tool的任意脚本入口。"""
        if sum(len(f['text'].encode()) for f in files)>LIMITS['input_bytes']:
            raise SandboxPolicyError('输入超过限额。')
        with tempfile.TemporaryDirectory(prefix='aiws-bwrap-') as folder:
            root=Path(folder); inputs=root/'input';inputs.mkdir()
            names={}
            for item in files:
                if not re.fullmatch(r'[A-Za-z0-9-]{1,36}',item['id']):
                    raise SandboxPolicyError('输入ID不合法。')
                (inputs/(item['id']+'.txt')).write_text(item['text'])
                names[item['id']]=item.get('name',item['id'])
            (inputs/'manifest.json').write_text(json.dumps(names,ensure_ascii=False))
            (root/'main.py').write_bytes(source)
            args=['/usr/bin/bwrap','--unshare-all','--die-with-parent','--new-session','--cap-drop','ALL']
            for path in ('/usr','/lib','/lib64'):
                if Path(path).exists(): args+=['--ro-bind',path,path]
            args+=['--ro-bind',str(inputs),'/input','--ro-bind',str(root/'main.py'),'/main.py',
                '--ro-bind',str(Path(__file__).with_name('bwrap_bootstrap.py')),'/bootstrap.py',
                '--dir','/tmp','--chdir','/','--remount-ro','/',
                '--','/usr/bin/python3','-I','-B','-S','/bootstrap.py']
            process=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,stdin=subprocess.DEVNULL,
                env={'PATH':'/usr/bin:/bin','LC_ALL':'C.UTF-8','AIWS_SANDBOX_TRANSPORT':'stdout'},
                close_fds=True,start_new_session=True)
            selector=selectors.DefaultSelector()
            selector.register(process.stdout,selectors.EVENT_READ,'stdout')
            selector.register(process.stderr,selectors.EVENT_READ,'stderr')
            output=[];size=0;deadline=time.monotonic()+seconds
            try:
                while selector.get_map():
                    if cancelled and cancelled(): raise SandboxUnavailable('任务已取消或失去执行权。')
                    if time.monotonic()>deadline: raise SandboxUnavailable('沙箱运行超时。')
                    for key,_ in selector.select(.05):
                        data=os.read(key.fd,65536)
                        if not data: selector.unregister(key.fileobj);continue
                        size+=len(data)
                        if size>max_bytes: raise SandboxUnavailable('沙箱输出超过限额。')
                        if key.data=='stdout':output.append(data)
                if process.wait(timeout=1)!=0: raise SandboxUnavailable('隔离脚本失败；未导入产物。')
                if cancelled and cancelled(): raise SandboxUnavailable('任务已取消或失去执行权。')
                return b''.join(output)
            finally:
                selector.close()
                if process.poll() is None:
                    try:
                        os.killpg(process.pid,signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=3)
                process.stdout.close();process.stderr.close()
