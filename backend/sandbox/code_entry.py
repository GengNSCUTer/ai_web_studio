"""隔离内部的通用Python入口；权限边界由namespace/seccomp而非此帮助函数提供。"""

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import re


class BoundedOutput(io.StringIO):
    def write(self, value):
        if self.tell() + len(value) > 65536:
            raise ValueError('标准输出超过65536字符')
        return super().write(value)


def main():
    artifacts = []
    output = BoundedOutput()

    def emit_artifact(name, content):
        if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]+\.(md|txt|json|csv)', name)
                or not isinstance(content, str) or '\x00' in content or len(artifacts) >= 16):
            raise ValueError('产物名称、类型或数量不符合要求')
        artifacts.append({'name': name, 'content': content})
        if sum(len(item['content'].encode()) for item in artifacts) > 512 * 1024:
            raise ValueError('产物超过512KiB')

    # 任意Python不是Python层白名单；用户可导入标准库，真实隔离靠操作系统执行。
    source = Path('/input/program.py').read_text(encoding='utf-8')
    with redirect_stdout(output):
        exec(compile(source, '<submitted>', 'exec'), {
            '__name__': '__main__', 'emit_artifact': emit_artifact,
            'input_dir': Path('/input/files'),
        })
    print(json.dumps({'stdout': output.getvalue(), 'artifacts': artifacts}, ensure_ascii=False))


if __name__ == '__main__':
    main()
