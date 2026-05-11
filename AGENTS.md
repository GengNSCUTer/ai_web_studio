# AI Web Studio 协作规范（Codex）

本文件用于约束后续在本项目中的默认交付流程。  
目标：保证“开发进度、文档、飞书、GitHub”四者持续同步，避免遗漏。

## 1. 完成开发后的固定步骤

每次完成一个可验证功能点（或一次阶段性修复）后，按顺序执行：

1. 更新本地项目文档（`docs/`）
2. 同步文档到飞书
3. 向用户汇报本次变更与验证结果
4. 主动询问是否同步到 GitHub（私有仓库）

不允许跳过第 4 步的询问。

## 2. 本地文档更新范围

至少检查并按需更新以下文档：

- `docs/05_详细需求清单.md`：需求状态（完成/未完成/调整）
- `docs/06_技术实现设计.md`：实现方案、接口或架构变化
- `docs/07_当前实现进展与下一步计划.md`：当前进度与下一步计划

若本次变更涉及上下文治理参数或策略，额外更新：

- `docs/09_上下文配置与动态预算说明.md`

## 3. 飞书同步要求

默认将“阶段总结或增量更新”追加到飞书项目总览文档：

- 总览文档：
  `https://my.feishu.cn/docx/Mx2KdT3FboSwn3xw6PucH0N2nOf`

目录映射参考：

- `docs/11_飞书文档目录补充_2026-05-10.md`

同步命令（示例）：

```bash
cd /disk2/gengnan/ai_web_studio
lark-cli docs +update \
  --doc https://my.feishu.cn/docx/Mx2KdT3FboSwn3xw6PucH0N2nOf \
  --mode append \
  --markdown @./docs/12_飞书同步更新_2026-05-10.md
```

注意：

- `--markdown @文件路径` 必须使用当前目录下的相对路径
- 同步后需在回复中说明是否成功（包含成功/失败结论）

## 4. GitHub 同步要求

每次完成并同步飞书后，必须主动询问用户：

- “是否现在同步到 GitHub 私有仓库？”

仓库信息（当前）：

- `git@github.com:GengNSCUTer/ai_web_studio.git`
- 默认分支：`main`

若用户同意，再执行 `git add/commit/push`；若用户未确认，不自动推送。

## 5. 安全与提交边界

- 禁止提交密钥、口令、Token、`.env` 等敏感文件
- 推送前检查 `.gitignore` 是否覆盖运行时文件（如 `uploads/`, `.next/`, `node_modules/`）
- 涉及配置默认值时，不在代码中硬编码真实 API Key

## 6. 回复模板（简版）

每次交付建议使用以下顺序回复用户：

1. 本次完成了什么
2. 文档已更新到哪些本地文件
3. 飞书是否同步成功
4. 是否现在同步到 GitHub

