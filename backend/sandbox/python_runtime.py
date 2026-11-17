"""通用执行仅使用单独准备的只读解释器，不挂载业务venv。"""

import json
import os
from pathlib import Path

from sandbox.runner import SandboxUnavailable


def runtime_root():
    configured = os.environ.get('AIWS_SANDBOX_PYTHON_ROOT')
    root = Path(configured) if configured else Path(__file__).resolve().parents[2] / '.runtime/sandbox/python'
    root = root.resolve()
    try:
        metadata = json.loads((root / 'runtime.json').read_text())
        version = metadata['version']
        if (not isinstance(version, list) or len(version) != 3 or version[:2] != [3, 12]
                or not (root / 'bin/python3').is_file()
                or not (root / 'lib/python3.12/os.py').is_file()
                or (root / 'lib/python3.12/site-packages').exists()):
            raise ValueError()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise SandboxUnavailable('通用Python运行时未准备，请部署独立Python3.12标准库环境。') from exc
    return root
