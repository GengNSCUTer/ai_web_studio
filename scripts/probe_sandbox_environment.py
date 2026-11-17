"""P0-B只读环境探测：只运行固定诊断，不接受用户脚本，不修改宿主配置。"""

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import stat
import subprocess
import tempfile


def run_fixed(command):
    """子进程不继承凭据；诊断有时限，失败也留下明确结果。"""
    try:
        result = subprocess.run(command, env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"},
                                close_fds=True, capture_output=True, text=True, timeout=10)
        return {"exit_code": result.returncode, "stdout": result.stdout[:4096], "stderr": result.stderr[:512]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"exit_code": None, "error": type(exc).__name__}


def inspect_environment():
    tools = {name: shutil.which(name, path="/usr/bin:/bin") for name in ("bwrap", "unshare", "nsjail", "wasmtime")}
    result = {"kernel": platform.release(), "uid": os.getuid(), "tools": tools,
              "phase": "environment_probe_only", "execution_enabled": False}
    controllers = Path('/sys/fs/cgroup/cgroup.controllers')
    if not controllers.exists():
        controllers = Path('/sys/fs/cgroup/unified/cgroup.controllers')
    result['cgroup_v2_controllers'] = controllers.read_text().split() if controllers.exists() else []
    result['namespace_probe'] = (run_fixed([tools['unshare'], '--user', '--map-root-user', '--mount', '--net',
        '--pid', '--ipc', '--uts', '--fork', '--mount-proc', '/usr/bin/true']) if tools['unshare'] else {'error': 'missing_unshare'})
    bwrap = tools['bwrap']
    if not bwrap or Path(bwrap).stat().st_mode & (stat.S_ISUID | stat.S_ISGID):
        result['bwrap_probe'] = {'error': 'missing_or_privileged_binary'}
        return result
    result['bwrap_version'] = run_fixed([bwrap, '--version'])
    # 仅将本次合成输入绑定进去；不挂载宿主根目录、HOME、工作区或凭据。
    with tempfile.TemporaryDirectory(prefix='aiws-isolation-probe-') as directory:
        sample = Path(directory) / 'sample.txt'
        sample.write_text('synthetic-input')
        marker = Path(directory) / 'host-only.txt'
        marker.write_text('synthetic-host-marker')
        command = [bwrap, '--unshare-all', '--die-with-parent', '--new-session', '--cap-drop', 'ALL']
        for location in ('/usr', '/lib', '/lib64'):
            if Path(location).exists():
                command += ['--ro-bind', location, location]
        command += ['--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp', '--dir', '/input',
                    '--ro-bind', str(sample), '/input/sample.txt', '--remount-ro', '/', '--',
                    '/usr/bin/python3', '-I', '-S', '-c', CHILD_PROBE, str(marker)]
        result['bwrap_probe'] = run_fixed(command)
        result['host_input_unchanged'] = sample.read_text() == 'synthetic-input'
    return result


CHILD_PROBE = r'''
import json,os,socket,sys
def denied(path):
    try:
        with open(path,'w') as target: target.write('probe')
        return False
    except OSError:
        return True
status=dict(line.split(':',1) for line in open('/proc/self/status') if ':' in line)
checks={'host_marker_hidden':not os.path.exists(sys.argv[1]),
        'host_home_hidden':not os.path.exists('/home'),
        'host_credentials_hidden':not os.path.exists('/etc/shadow'),
        'docker_socket_hidden':not os.path.exists('/var/run/docker.sock'),
        'input_readable':open('/input/sample.txt').read()=='synthetic-input',
        'input_readonly':denied('/input/sample.txt'), 'root_readonly':denied('/root-write-probe'),
        'no_new_privileges':status.get('NoNewPrivs','').strip()=='1',
        'no_effective_capabilities':int(status.get('CapEff','1').strip(),16)==0,
        'environment_is_minimal':set(os.environ).issubset({'PATH','LC_ALL','LC_CTYPE','PWD'})}
for port in (32007,35433):
    with socket.socket() as sock:
        sock.settimeout(.3)
        checks['host_port_'+str(port)+'_unreachable']=sock.connect_ex(('127.0.0.1',port))!=0
print(json.dumps(checks,sort_keys=True))
sys.exit(0 if all(checks.values()) else 1)
'''


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = json.dumps(inspect_environment(), ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(report)
    print(report)
