"""审核脚本：只读取本次授权的文本副本，在临时产物区生成清单。"""

import json
from pathlib import Path

items = []
names = json.loads(Path("/input/manifest.json").read_text(encoding="utf-8"))
for file in sorted(Path("/input").glob("*.txt")):
    text = file.read_text(encoding="utf-8")
    name = str(names.get(file.stem, file.name)).replace("\n", " ").replace("\r", " ")
    # 文件名是资料元数据，不能让名称变成 Markdown 图片或链接指令。
    for char in ("\\", "[", "]", "(", ")", "|", "<", ">"):
        name = name.replace(char, "\\" + char)
    items.append({"file": name, "characters": len(text), "lines": len(text.splitlines())})
Path("/out/inventory.json").write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
try:
    from tabulate import tabulate
except ImportError:
    body = "\n".join(f"- {x['file']}：{x['characters']} 字符，{x['lines']} 行" for x in items)
else:
    body = tabulate([[x["file"], x["characters"], x["lines"]] for x in items],
                    headers=["授权副本", "字符数", "行数"], tablefmt="github")
Path("/out/inventory.md").write_text("# 授权资料清单\n\n" + body + "\n", encoding="utf-8")
print(f"已整理 {len(items)} 份授权副本；未修改工作区原文件。")
