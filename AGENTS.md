# AI Web Studio 协作规范（Codex）

本文件用于约束后续在本项目中的默认交付流程。  
目标：保证“开发进度、文档、飞书、GitHub”四者持续同步，避免遗漏。

## 1. 完成开发后的固定步骤

每次完成一个可验证功能点（或一次阶段性修复）后，按顺序执行：

1. 根据改动范围执行必要测试 / 检查，确认没有明显 bug 或回归
2. 更新本地项目文档（`docs/`）
3. 同步文档到飞书
4. 向用户汇报本次变更与验证结果
5. 主动询问是否同步到 GitHub（私有仓库）

不允许跳过第 1 步的验证，也不允许跳过第 5 步的询问。

## 1.1 测试与检查要求

每次代码修改完成后，必须按改动范围选择合适的验证方式，并在最终回复中说明执行结果。

最低要求：

- 后端改动：至少执行相关后端单测；涉及 import / model / route / service 时额外执行 `compileall`
- 前端改动：至少执行相关文件 ESLint；涉及页面、路由、类型或构建配置时额外执行 `npm run build`
- 涉及前端页面、关键交互或全链路功能时，必须使用 Playwright 做至少一次浏览器冒烟测试，检查页面加载和 console error；如果无法执行，必须说明原因
- 文档-only 改动：至少检查 `git diff` 和敏感信息，不要求跑完整构建
- 全链路阶段性功能：尽量执行后端相关测试集合 + 前端 build
- RAG / 知识库索引检索测试优先使用固定测试账号，不再随意注册临时账号；当前固定测试账号为 `1528713326@qq.com`
- RAG embedding / rerank 测试优先使用 API Provider，不使用本地 Ollama 做索引检索压测；当前默认组合为 `siliconflow / BAAI/bge-m3` 与 `siliconflow / BAAI/bge-reranker-v2-m3`
- 本地 Ollama 连接测试使用 `http://127.0.0.1:11435`；注意当前设置表中的 `ollama_base_url` 也是聊天 provider base_url，若账号当前为 `openai-compatible`，不要强行改成 Ollama 地址

当前常用验证命令：

```bash
cd /disk2/gengnan/ai_web_studio
PYTHONPATH=backend /disk2/gengnan/conda_envs/ai_web_studio/bin/python -m compileall -q backend/app backend/tests
PYTHONPATH=backend /disk2/gengnan/conda_envs/ai_web_studio/bin/python -m unittest backend.tests.test_knowledge_service

cd /disk2/gengnan/ai_web_studio/frontend
npx eslint <changed-files>
npm run build
```

如果某项测试无法执行，必须说明原因，不要隐瞒。

## 2. 本地文档更新范围

至少检查并按需更新以下文档：
- `docs/04_智能问答网页方案.md`：整体方案（更新当前整体方案介绍）
- `docs/05_详细需求清单.md`：需求状态（完成/未完成/调整）
- `docs/06_技术实现设计.md`：实现方案、接口或架构变化
- `docs/07_当前实现进展与下一步计划.md`：当前进度与下一步计划

若本次变更涉及上下文治理参数或策略，额外更新：
- `docs/08_上下文治理研究与实施路线.md`
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
  --api-version v2 \
  --doc https://my.feishu.cn/docx/Mx2KdT3FboSwn3xw6PucH0N2nOf \
  --command append \
  --content @./docs/12_飞书同步更新_2026-05-10.md
```

注意：

- `--content @文件路径` 必须使用当前目录下的相对路径
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

## 6. 代码健康巡检要求

随着功能逐步增多，必须定期做“代码健康巡检”，避免项目继续膨胀成难维护的结构。

建议触发时机：

1. 每完成一批较大的功能点后
2. 每次前后端核心文件明显变大后
3. 在准备进入下一阶段主线开发前
4. 在准备做 GitHub / 飞书阶段性同步前

巡检重点：

- 是否存在明显冗余逻辑、重复实现、无用状态、无用接口
- 是否有“为了兼容旧逻辑”留下但已无调用路径的代码
- 是否有职责混乱的超大文件，需要拆分
- 是否有可以通过抽象层收敛的 provider-specific 逻辑
- 是否缺少基本验证脚本，导致重构风险过高

当前建议优先使用的 skill / 能力：

- `vercel-react-best-practices`
  - 用于 React / Next 前端代码审查、性能模式检查、组件结构优化
- `next-best-practices`
  - 用于 Next.js 目录约定、RSC 边界、路由处理、hydration 风险审查
- `webapp-testing`
  - 用于关键交互回归测试、前端行为验证、UI 冒烟测试
- `find-skills`
  - 用于继续查找适合当前阶段的新 skill
- `autoresearch`
  - 用于“有明确指标”的性能优化或实验型调参，不用于普通一次性修 bug
- `gsd`
  - 用于阶段性代码库摸底、复杂问题系统性排查、里程碑前验证和较大开发阶段的结构化推进
  - 不适合每个小功能、小修复都默认触发

说明：

- 目前没有找到一个高质量、可直接安装且专门覆盖“通用代码审查 + 无用代码清理 + 架构瘦身”的现成 skill。
- 因此当前策略是：用上面的前端 / Next / 测试 skill 做专项巡检，再结合人工 code review 做结构治理。
- 如果后续这类巡检频率继续增加，可以专门为本项目创建一个自定义 `quality-review` skill。
- `gsd` 已安装，但它本质上是重型 project-management / workflow skill，不是纯 code review skill。
- `gsd` 安装结果的风险提示偏高，因此使用时要限制场景：
  - 适合：`map-codebase`、`debug`、`verify-work` 这类阶段性任务
  - 不适合：普通小改动、快速热修、无明确范围的一次性代码清理

## 7. 回复模板（简版）

每次交付建议使用以下顺序回复用户：

1. 本次完成了什么
2. 文档已更新到哪些本地文件
3. 飞书是否同步成功
4. 是否现在同步到 GitHub
