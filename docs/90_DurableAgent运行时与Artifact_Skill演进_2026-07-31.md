# Durable Agent 运行时与 Artifact / Skill 演进（2026-07-31）

## 1. 本轮目标与项目定位

本轮围绕六个方向继续增强 AI Web Studio：

1. 泛化 Durable Agent Runtime；
2. Tool Artifact 与上下文管理；
3. Skill / Plugin 基础；
4. 项目文件版本闭环；
5. 安全扩展工具能力；
6. 生产观测与评测。

项目仍定位为：

> 云侧优先的项目知识工作 Agent Workspace，围绕项目文档、知识库、上下文、记忆和受控工具，完成检索、分析、生成与提案式修改。

当前不是允许模型任意执行 Shell、SQL、支付或外部发布的通用自治 Agent。模型负责有限规划；权限校验、Schema 校验、工具调用、Lease、重试、审批和持久化仍由确定性代码控制。

## 2. Durable Tool Runtime

### 2.1 当前支持范围

新增低风险只读 Tool DAG 的数据库持久化运行时：

```text
POST /api/agent-runtime/tool-runs
  -> 同一事务创建 AgentRun + AgentStep + AgentOutboxEvent + AgentCheckpoint
  -> HTTP 返回 202
  -> 独立 Worker 领取 Outbox Event
  -> 校验依赖和 Result Binding
  -> 调用真实 ToolExecutor
  -> 完整结果保存为 AgentArtifact
  -> 更新 Step / Run / Checkpoint
```

运行时当前只接受同时满足以下条件的工具：

- `risk_level=low`；
- `read_only=true`；
- 已进入当前用户可见的 Tool Catalog；
- 参数通过 Tool JSON Schema 校验。

文件修改不进入这条通用只读 Worker 链，而继续走专用的 `PatchDraft -> Approval -> CAS -> FileRevision` continuation。Shell、SQL、删除、支付、邮件发送、外部发布和任意 HTTP 写接口仍然不开放。

### 2.2 可靠性机制

- PostgreSQL Outbox：Run、Step 和待执行事件同事务提交，避免业务状态存在但执行事件未创建。
- Lease：事件被 Worker 领取后记录 owner、过期时间和 heartbeat。
- Fencing：完成结果时必须同时匹配 `lease_owner` 与 `lease_version`，旧 Worker 即使晚返回也不能覆盖新 Worker。
- 有限重试：真实工具请求失败后指数退避，达到 `max_attempts` 后进入数据库 DLQ 状态。
- Checkpoint：每次入队、成功、跳过、重试和死信都保存版本化观察状态。
- DAG：支持显式 `depends_on`，入队前检测不存在的依赖、自依赖和环路。
- Result Binding：下游只可用声明式 JSON Pointer 读取上游 Artifact，绑定后再次执行完整 Schema 校验。
- 幂等：默认由规范化请求哈希生成幂等键；客户端显式幂等键会绑定完整请求摘要，同键不同请求返回冲突。
- Scope 校验：project、conversation 和 assistant message 必须属于当前用户，且三者关联一致。

这里只提供 At-least-once 的执行骨架，不宣称 Exactly-once。当前通用 Worker 限定为只读工具，所以崩溃后重试不会制造外部写副作用；未来开放写工具前，仍需逐工具定义业务幂等键、结果核对和补偿策略。

### 2.3 Worker 入口

```bash
cd backend
./scripts/run_agent_tool_worker.sh
```

当前脚本处理完可领取任务后退出，适合由 supervisor、cron 或容器任务调度。常驻轮询、告警和水平扩缩容仍属于部署层后续工作。

## 3. Tool Artifact 与上下文

新增 `AgentArtifact` 保存 Tool Step 的完整结构化结果：

- Artifact 绑定 run、step、user 和 project scope；
- 保存内容哈希、预览、字符数和完整 JSON；
- Step 结果只保存 Artifact ID 与来源数量，不把大结果复制到多个状态字段；
- 完整 Artifact 默认不自动注入 Prompt，避免长工具结果挤占 Token 预算。

新增受控工具：

```text
agent.artifacts.list
agent.artifacts.read
```

读取使用 opaque Artifact ID，始终校验当前用户和项目；列表最多返回 20 条，单次正文最多返回 12000 字符。后续可以在 Context Assembly 中基于任务相关性选择预览或按需读取正文，但当前还没有让 Planner 自动遍历所有 Artifact。

## 4. Skill / Plugin 基础

新增内置 declarative Skill manifest 与用户安装状态。当前 Skill 只声明：

- `skill_key` 与版本；
- 名称和用途；
- 依赖的 Tool Keys；
- 风险声明。

Manifest 不允许携带 Python、Shell、任意 URL、凭证或直接权限。Skill 不能创造新能力，只能编排已经通过 Tool/MCP 安全边界的能力。设置页会展示 Skill、依赖 Tool、缺失能力及启用状态。

当前准确状态是“审计过的内置 declarative Skill + 用户安装状态”，不是第三方插件市场。以下能力仍未实现：

- 用户上传任意 Skill；
- Manifest 签名、来源验证、审核和版本升级；
- Marketplace 浏览、评分和发布；
- 把已启用 Skill 自动注入 Planner Prompt；
- Local Companion 或端侧本机权限代理。

## 5. 项目文件版本闭环

文件写入继续遵守专用安全链：

```text
读取 ACL 范围内文件
  -> 生成 PatchDraft 与统一 Diff
  -> 创建 AgentApproval
  -> 用户取得一次性 challenge 并确认
  -> 校验参数哈希、基线内容哈希和当前 revision
  -> CAS 应用
  -> 创建新 FileRevision
```

新增文件修订历史接口，以及“将历史版本作为新修改提案”的恢复接口：

```text
GET  /api/agent-runtime/files/{file_id}/revisions
POST /api/agent-runtime/files/{file_id}/revisions/{revision_id}/restore
```

Restore 不会直接覆盖文件，而是生成新的 Diff、Run、Step、PatchDraft 和 Approval；只有确认并通过 CAS 后才产生新 revision。前端当前先提供只读版本历史，恢复确认 UI 后续再接，不提供绕过审批的直接恢复按钮。

## 6. 生产观测

新增 `ChatRuntimeMetric`，在 assistant 消息完成持久化后 best-effort 保存单轮统计。观测写入失败不会改变聊天结果。

```text
GET /api/agent-runtime/metrics
```

当前汇总包括：

- Agent Run / Step / Outbox 状态数量；
- Artifact 总数；
- Tool Call 状态；
- RAG retrieval run、召回 Chunk 和实际注入 Chunk；
- Chat 输入、输出与 cached input token；
- 外部来源检索数和实际注入数。

Chat 与 RAG 明确返回最近 500 条记录的观察窗口，避免把窗口统计误读为全量历史。后续仍需增加时延分位数、重试率、DLQ 告警、Selector Recall@K、Planner accuracy 和 Answer groundedness 的持续评测面板。

## 7. JSONL 是否应成为会话存储

### 7.1 当前状态

AI Web Studio 当前不是用 JSONL 作为会话主存储。Conversation、Message、Agent Run/Step、Tool Trace、RAG Log 和权限关系都保存在 PostgreSQL 关系表中。

本机当前 Codex 状态目录可以确认存在 `history.jsonl`、`session_index.jsonl` 和按日期组织的 `rollout-*.jsonl`。由于官方 Codex 手册抓取返回 HTTP 403，本项目不据此推断或宣称 Codex 的全部内部实现，只借鉴本机可验证的文件组织方式和 append-only 日志模式。

### 7.2 为什么不替换 PostgreSQL

JSONL 很适合单机 CLI 的追加写、顺序回放和离线分析，但不适合作为本项目在线业务真相源：

- 多用户 ACL 查询困难；
- 跨会话、消息、项目、Artifact 的关系查询成本高；
- 缺少关系数据库事务和约束；
- 多实例并发写、分页、索引和状态 CAS 需要重新实现；
- 删除、脱敏、保留策略和审计边界更难控制。

### 7.3 本轮采用方式

在现有会话导出中新增 `format=jsonl`。导出结果是可移植的版本化事件流：

```json
{"schema_version":"aiws.conversation.v1","event_id":"message:<id>","event_type":"message.snapshot","timestamp":"...","conversation_id":"...","message_id":"...","data":{}}
```

特性：

- 一行一个独立 JSON 对象；
- 稳定 `event_id`；
- 带 `schema_version`，便于后续兼容升级；
- 会话、滚动摘要、消息按事件记录；
- 不导出服务器本地 `storage_path`；
- ZIP 导出同时包含 Markdown、JSON 和 JSONL。

因此当前采用“双层设计”：

```text
PostgreSQL = 在线事务、权限和状态真相源
JSONL      = 导出、冷存档、调试回放、离线评测输入
```

后续如需完整 Agent Run 回放，可再定义独立的 `aiws.agent-run.v1` 事件 Schema，导出 Run、Step、Checkpoint、Approval 和 Artifact 引用；不应直接把数据库行无版本地 dump 成 JSONL。

## 8. 验证结果

- `compileall`：通过；
- 后端完整单测：共运行 265 项，整体 `OK`，其中 3 项按外部集成条件跳过；
- 前端 ESLint：通过；
- Next production build：通过；
- PostgreSQL runtime schema：新表和新增列已实际创建并核对；
- Playwright：桌面与 390px 移动设置页、Skill 数据、JSONL 导出和 console error 检查通过。

## 9. 后续优先级

1. 为数据库 DLQ 增加受控人工重放与告警，而不是自动无限重试；
2. 为常驻 Durable Worker 增加优雅停机、健康检查和部署级进程监控；
3. 让已启用 Skill 经过候选召回后进入 Planner，但仍不能绕过 Tool 权限；
4. 增加 Agent Run JSONL 导出与离线 replay harness；
5. 增加文件恢复的 Diff/Approval 前端 continuation；
6. 将启动时 DDL 迁移到 Alembic 等版本化 migration。

## 10. 合并前代码审查收口

按并发正确性、权限、错误分类、前端状态与可运维性逐行审查后，又完成以下修复：

- Worker 改为空队列不退出的常驻轮询，循环异常限定在单轮内；
- 执行期用独立 Session 定期续租，Heartbeat 同时更新 Step 和 Outbox Lease；
- 修正并行 Step 未结束时 Run 被误标为 `queued`，并取消依赖等待轮询的无意义 Checkpoint 膨胀；
- Durable Result Binding 与主 Tool Workflow 统一为 `metadata.raw` 受限 JSON Pointer，禁止把 `display_text` 当参数执行；
- 区分永久业务错误与可重试 Provider/传输错误，避免无效重试进入 DLQ；
- 文件恢复的可选 Conversation/Assistant Message 也必须属于当前用户和项目；
- Skill 缺少凭据时仍允许用户关闭已启用项，并收口首次并发安装的唯一键竞争。

当前非阻塞维护项：`durable_tool_runtime.py` 职责已较多，下一阶段应在测试保护下拆分为 Run 创建、调度/租约、Worker 执行和状态收口模块。
