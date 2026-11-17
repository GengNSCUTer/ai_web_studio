"""从可信已安装Python3.12复制最小环境；不安装依赖，不修改源环境。"""

import argparse
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def prepare(executable, target):
    executable = Path(executable).resolve()
    target = Path(target).resolve()
    if target.exists():
        raise ValueError('目标已存在；禁止覆盖运行中的解释器，请使用新的目录。')
    # 仅管理员部署命令使用，路径不接受自Web或模型请求。
    info = json.loads(subprocess.check_output([str(executable), '-I', '-S', '-c',
        'import sys,sysconfig,json;print(json.dumps({"version":list(sys.version_info[:3]),"stdlib":sysconfig.get_path("stdlib")}))']))
    if info['version'][:2] != [3, 12]:
        raise ValueError('只支持维护中的Python3.12标准库。')
    stdlib = Path(info['stdlib'])
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent, prefix='python-build-') as tmp:
        root = Path(tmp) / 'runtime'
        (root / 'bin').mkdir(parents=True)
        (root / 'lib').mkdir()
        shutil.copy2(executable, root / 'bin/python3')
        shutil.copytree(stdlib, root / 'lib/python3.12', ignore=shutil.ignore_patterns(
            'site-packages', 'test', 'tests', '__pycache__', '*.pyc', 'ensurepip', 'idlelib', 'tkinter'))
        # 原生扩展引用的环境私有动态库单独复制，不复制整个conda/lib目录。
        pending = [executable, *list((stdlib / 'lib-dynload').glob('*.so'))]
        visited = set()
        while pending:
            library = pending.pop()
            if str(library) in visited:
                continue
            visited.add(str(library))
            report = subprocess.run(['ldd', str(library)], capture_output=True, text=True, check=False).stdout
            for match in re.finditer(r'=>\s+(/\S+)', report):
                source = Path(match.group(1))
                if str(source).startswith(('/lib/', '/lib64/', '/usr/lib/')):
                    continue
                dest = root / 'lib' / source.name
                if not dest.exists():
                    shutil.copy2(source, dest)
                    pending.append(source)
        (root / 'runtime.json').write_text(json.dumps({'version': info['version'], 'packages': 'stdlib_only'}))
        root.rename(target)
    return info['version']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python', required=True)
    parser.add_argument('--target', required=True)
    args = parser.parse_args()
    print(json.dumps({'version': prepare(args.python, args.target), 'result': 'prepared'}))
