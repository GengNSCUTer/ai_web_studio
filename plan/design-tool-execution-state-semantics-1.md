---
goal: 为 AI Web Studio 的同步 Tool Workflow、Durable Runtime 与审批链路建立统一的执行状态、质量决策和失败传播规范
version: 1.0
date_created: 2026-09-11
last_updated: 2026-09-11
owner: AI Web Studio
status: 'In progress'
tags: [architecture, tool-workflow, agent-runtime, safety, durable-run]
---

# Introduction

![Status: In progress](https://img.shields.io/badge/status-In%20progress-yellow)

本计划把 Tool 调用从“有固定轮数的 Chat 循环”演进为按运行模式、预算、权限和持久化能力受控的执行系统。阶段 0 只完成规范、当前实现映射与验收定义；不会改动线上执行行为、工具范围、权限边界或同步轮数。阶段 1 起按本计划逐项实现，每次只落一个可独立回归的能力点。

## 1. Requirements & Constraints

- **REQ-001**: 同步 Tool Workflow 必须使用独立的执行生命周期、结果质量和后续动作三个维度；不得再以单一 `success/error` 字符串表达所有语义。
- **REQ-002**: 任意依赖边只有在上游执行成功且质量为 `valid` 时才能解锁；等待审批、被阻断、超时、取消、质量不合格均不得解锁下游。
- **REQ-003**: 多个独立只读 Tool 并发时，部分成功必须可作为最终回答证据；单分支失败不得无条件使整个 Chat 失败。
- **REQ-004**: 每一次同步 Agent Run 必须记录规范化 Tool 调用指纹，防止 Planner 跨 replan 重复同一个已经不可重试的 Tool + 参数组合。
- **REQ-005**: Tool 输出必须始终作为不可信证据；它不能提升权限、改变预算、绕过审批、直接决定下一次执行或成为可信系统指令。
- **REQ-006**: 长任务只能通过用户可见、可确认的 Durable handoff 进入 Durable Runtime；模型不得自行将普通 Chat 升级为后台无限任务。
- **REQ-007**: 本计划的最终模型必须兼容现有 `AgentRun`、`AgentStep`、`AgentCheckpoint`、`AgentApproval`、`PatchDraft`、`FileRevision`、Outbox、Lease、Fencing、Retry、DLQ 和受控重放能力。
- **SEC-001**: Executor 继续是 Schema、用户/项目 ACL、凭据、SSRF、风险等级和副作用审批的最终边界；Planner、Workflow、Skill、Trace 或 Durable Worker 均不能替代该边界。
- **SEC-002**: 不新增 Bash、Shell、SQL、删除、任意本机路径写入、支付、邮件、外部发布或任意 HTTP 写入能力。
- **SEC-003**: `full_workspace` 只能自动应用已有范围内、具备 ACL、Diff、Revision CAS 约束的工作区文件修改；它不代表主机完全访问或外部副作用授权。
- **CON-001**: 当前同步 Chat 上限保持“最多 5 轮 `plan -> execute -> observe`，每份 ToolPlan 最多 5 次调用”，直到运行模式和多维 Budget 阶段完成；不得把常量直接放大为无限循环。
- **CON-002**: 当前 Durable Runtime 只允许低风险、只读、已审核 Tool DAG；它尚未接通 Chat 自动 handoff，也尚未支持持久化 Planner continuation。
- **CON-003**: 当前工作区存在尚未提交的 Tool Quality Feedback 改动；后续任务必须保留并在阶段 1 中吸收其安全反馈语义，不得 reset、checkout 或覆盖。
- **GUD-001**: 每一实现阶段只增加一个核心能力点，先完成定向测试、完整后端回归、编译检查和文档更新，再进入下一阶段。
- **GUD-002**: 计划中的状态、动作与错误码由代码侧枚举和 Policy 决定；LLM 只能提出受限计划或建议，不能降低 Tool 的风险级别、依赖强度或审批要求。
- **PAT-001**: 采用“状态机 + 质量门 + 预算 + 权限 + 持久化”分层模式，而不是依赖更长 Prompt 或更大的轮数来修复执行问题。

## 2. Implementation Steps

### Implementation Phase 0 — 已完成：规范与当前实现映射

| Goal | Description | Completed | Date |
|------|-------------|-----------|------|
| GOAL-001 | 固化同步 Workflow、Durable Runtime、Approval 与最终回答之间的统一语义，作为后续实现的不可变验收基线。 | ✅ | 2026-09-11 |

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | 审查 `backend/app/services/tools/workflow.py`、`external_context_service.py`、`quality.py`、`executor.py`、`durable_tool_runtime.py`、`agent_runtime.py` 与对应测试，记录当前真实边界：同步五轮、单计划五调用、DAG/Binding、质量门、Approval、Durable Run。 | ✅ | 2026-09-11 |
| TASK-002 | 定义三维语义：执行状态 `pending/ready/running/succeeded/failed/blocked/waiting_approval/cancelled/timed_out`；质量状态 `valid/uncertain/invalid/not_applicable`；代码决定的后续动作 `continue/fallback/replan/clarify/finalize_partial/handoff_durable/stop`。 | ✅ | 2026-09-11 |
| TASK-003 | 定义依赖转移：只有 `succeeded + valid` 解锁严格依赖；其它终态都以确定原因阻断下游。独立分支按聚合规则处理，不能因无关分支失败抹掉可用 evidence。 | ✅ | 2026-09-11 |
| TASK-004 | 定义 Tool Result 的信任边界：Planner 只能收到 Tool-specific、白名单化的 observation projection；最终回答可引用 evidence，但 evidence 永远不能成为指令或权限来源。 | ✅ | 2026-09-11 |
| TASK-005 | 定义运行模式与 Durable handoff 的目标边界：运行模式和预算由用户/代码决定；第一次 handoff 只转交已验证的静态低风险只读 DAG，不宣称已支持持久化 ReAct/Planner continuation。 | ✅ | 2026-09-11 |

### Implementation Phase 1 — 同步 DAG 状态机与失败传播

| Goal | Description | Completed | Date |
|------|-------------|-----------|------|
| GOAL-002 | 让每个同步 Tool Step、每条依赖边和每次 replan 都具有统一、稳定、可审计的状态和确定动作。 | ✅ | 2026-09-11 |

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-006 | 在 `backend/app/services/tools/workflow.py` 新增不可变 `ToolStepOutcome` 数据结构，字段至少包含 `call_id`、`tool_key`、执行状态、质量状态、质量原因、错误类别、下游动作、依赖、是否可进入 Prompt、规范化调用指纹和耗时。`ToolWorkflowResult` 增加 `step_outcomes` 与 Run 聚合状态，不删除现有 `events` 或 `feedback`。 | ✅ | 2026-09-11 |
| TASK-007 | 在 `ToolWorkflowService.run()` 以 Outcome 驱动 pending/ready/running/terminal 转移；将现有 `completed`、`failed`、`quality_by_call_id` 等局部集合收敛为 Outcome 索引。保持现有 Result Binding、fallback、Schema 复验和 Executor 边界。 | ✅ | 2026-09-11 |
| TASK-008 | 在 `ExternalContextService.build_context()` 为单次同步请求维护 Run-scoped call ledger。指纹使用 Tool Key 与规范化 arguments 的稳定哈希；非可重试失败或质量不合格的相同指纹在后续 replan 中返回受控 `duplicate_across_run` outcome，不再次发起网络调用。 | ✅ | 2026-09-11 |
| TASK-009 | 将当前 `ToolWorkflowFeedback` 改为从 terminal Outcome 投影，保留受限字段；`replan`、`clarify`、`block`、`waiting_approval` 必须有不同的安全 observation 语义，且不能回灌 Provider 原始异常、URL、远端正文或未通过质量门的内容。 | ✅ | 2026-09-11 |
| TASK-010 | 扩展 `backend/tests/test_tool_workflow.py`、`test_tool_router.py`：覆盖严格依赖阻断、等待审批、质量失败、重复指纹、不同参数允许重试规划、并行批次隔离和 aggregate terminal state。 | ✅ | 2026-09-11 |

### Implementation Phase 2 — 全 Tool ResultQualityContract

| Goal | Description | Completed | Date |
|------|-------------|-----------|------|
| GOAL-003 | 把“Adapter 返回 JSON”提升为“结果在当前任务中具有业务有效性”，覆盖所有默认 Tool。 | ✅ | 2026-09-14 |

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-011 | 为 `backend/app/tool_manifests/default_tools.json` 中全部启用 Tool 声明质量语义；地图和搜索复核现有合同，Artifact、workspace list/search/read/propose/apply 新增合同或 Tool-specific evaluator。 | ✅ | 2026-09-14 |
| TASK-012 | 在 `backend/app/services/tools/quality.py` 增加受限的 evaluator 扩展点。它只能读取 Adapter 已规范化的 evidence envelope，不执行文本、不调用网络、不读取模型生成的规则。 | ✅ | 2026-09-14 |
| TASK-013 | 将“业务空结果”与“无效/不完整响应”分开。保持质量状态集合不扩张；通过 `result_semantics=empty_answer/evidence/approval_draft` 描述业务含义，避免把“未找到文件”错误标为质量失败。 | ✅ | 2026-09-14 |
| TASK-014 | 扩展 Executor、Workflow 和 Durable Worker 测试，覆盖每类默认 Tool 的正常、空结果、缺字段、目标/作用域不匹配、质量失败阻断和 Approval/Revision 完成态。 | ✅ | 2026-09-14 |
| TASK-011a | 阶段 2.1：为 `agent.artifacts.list`、`workspace.files.list/search/read` 声明 `allow_empty`、最少 Source 数与非空说明文本合同；正常无结果由本地 Provider 返回固定安全 Source。 | ✅ | 2026-09-11 |
| TASK-013a | 阶段 2.1：实现受限 `result_semantics`。普通空 evidence、未知语义和远端 MCP 的空答案自称失败关闭；仅本地可信 Adapter 的 `empty_answer` 且合同允许时有效。 | ✅ | 2026-09-11 |
| TASK-014a | 阶段 2.1：增加 Quality / Executor / Workspace / Artifact / Workflow Binding / Durable Artifact 的合法空结果与伪造语义回归；完整后端 `378 tests` 通过、`5 skipped`。 | ✅ | 2026-09-11 |
| TASK-012a | 阶段 2.2A：实现固定 `semantic_profile` 白名单、声明式 `profile_mapping` 和有界 JSON Pointer；统一检查证据、身份、集合与集合项，不允许动态执行规则。 | ✅ | 2026-09-14 |
| TASK-012b | 阶段 2.2A：用跨 Provider 离线 fixture 验证同一能力 Profile 只需更换字段映射即可复用，并覆盖空结果、缺字段、错误结构、越界和失败关闭边界。 | ✅ | 2026-09-14 |
| TASK-012c | 阶段 2.2B：统一 MCP `structuredContent`/文本 JSON 提取，接入 Tavily/高德专用 canonical Mapper；默认 manifest 使用业务 Profile，不以任意 `display_text` 放行。 | ✅ | 2026-09-14 |
| TASK-012d | 阶段 2.2C：将 Executor Schema 校验后的 normalized arguments 作为受控 request_context，完成天气、geo 城市和路线起终点的确定性请求/结果匹配。 | ✅ | 2026-09-14 |
| TASK-012e | 阶段 2.2B/C：补真实响应形态 fixture、坏响应回归和 8 个只读 Tool 的真实脱敏烟测；完整后端 `396 tests` 通过、`5 skipped`。 | ✅ | 2026-09-14 |
| TASK-011b | 提交前复审：补齐 Artifact read、文件预览/写入合同；16 个默认 Tool 全覆盖，动态 MCP 默认要求审核 Profile。 | ✅ | 2026-09-14 |
| TASK-013b | 收紧 `empty_answer/approval_draft` 的信任锚至项目内 Runner + 对应本地 Provider；补混合文本 JSON 与伪造 Adapter 的失败关闭测试。 | ✅ | 2026-09-14 |
| TASK-014b | 复审回归覆盖 Artifact 读取、文件 Profile、草案与 Revision 完成态、动态 MCP 失败关闭；完整后端 `403 tests` 通过、`5 skipped`。 | ✅ | 2026-09-14 |

### Implementation Phase 3 — 并发部分成功聚合

| Goal | Description | Completed | Date |
|------|-------------|-----------|------|
| GOAL-004 | 让多 Tool 并发任务以可解释的 `succeeded/partial/blocked/failed` 聚合状态收口，并只让相关依赖受到失败影响。 |  |  |

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-015 | 在同步 Workflow 中基于 Step Outcome 计算 Run aggregate，区分 valid evidence、受控失败摘要、等待审批和被阻断的下游；`tool_workflow_end` 使用稳定 aggregate status，而非“是否存在 source”。 |  |  |
| TASK-016 | 为独立分支定义默认软失败策略：成功分支可用于最终回答，失败分支只产生安全说明；严格 `depends_on` 仍硬阻断。不得接受 Planner 把安全关键依赖降级为可选的请求。 |  |  |
| TASK-017 | 在 `ExternalContextService` 中将 aggregate 状态映射为 `finalize_partial/replan/clarify/stop`；最终回答只接收 valid evidence，用户可看到简短、脱敏的不可用说明。 |  |  |
| TASK-018 | 为路线/天气/搜索等并发成功与单路失败、全路失败、严格下游依赖、等待审批四种组合建立端到端单测。 |  |  |

### Implementation Phase 4 — Tool Evidence Prompt Injection 边界

| Goal | Description | Completed | Date |
|------|-------------|-----------|------|
| GOAL-005 | 在已有脱敏与 System 行为合同之上，将外部 Tool 结果在 Planner、Binding、Prompt、Trace 与 Artifact 之间改为显式的可信度投影。 |  |  |

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-019 | 定义 `PlannerObservationProjection`，按 Tool 类型输出白名单事实。网页/文档正文不再作为下一轮 Planner 的默认原文观察；确有审阅需求时使用受限摘录和明确的 untrusted evidence 标签。 |  |  |
| TASK-020 | 调整 `ExternalContextService._build_observations()` 与 Planner prompt 组装：禁止把未经 projection 的 `display_text`、嵌套 raw metadata、远端 URL/异常正文交给 Planner。 |  |  |
| TASK-021 | 保持最终回答的引用能力，但在 `formatter.py` / `prompt_builder_service.py` 强化 evidence boundary；外部文本不能改变 Tool allowlist、权限、审批、预算、System 指令或用户身份。 |  |  |
| TASK-022 | 新增恶意网页、恶意工作区文档、恶意 MCP 返回的回归测试，验证它们不能引导 Planner 调用未授权 Tool、提升权限或泄露数据。 |  |  |

### Implementation Phase 5 — 运行模式、多维 Budget 与静态 Durable handoff

| Goal | Description | Completed | Date |
|------|-------------|-----------|------|
| GOAL-006 | 用显式运行模式和多维 Budget 取代单一“五轮”叙事，并让低风险长任务从 Chat 受控转交现有 Durable Runtime。 |  |  |

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-023 | 新增代码侧 `ToolRunPolicy` / `ToolRunBudget`，定义 `quick_chat`、`guided_research`、`workspace_review`、`edit_proposal`、`durable_task` 的允许 Tool 类别、模型轮次、总调用数、并发度、墙钟时间、单调用超时、重规划次数、evidence 字符/Token 与结果大小上限。 |  |  |
| TASK-024 | 在同步路径按 Budget 记录消耗与终止原因；预算耗尽时只允许 `finalize_partial`、`clarify` 或生成 handoff proposal，不能继续隐式扩轮。 |  |  |
| TASK-025 | 复用 `DurableToolRunService.enqueue()` 实现 Chat 到 Durable 的显式 handoff：用户确认后仅把已经过 Catalog/Scope/Skill digest 验证的静态低风险只读 DAG 入队，保存源 Chat/Plan/Budget 快照。 |  |  |
| TASK-026 | 不实现持久化 Planner continuation、任意动态 ReAct 或自动后台化；将它们记录为在静态 handoff 验证后才可评估的独立后续需求。 |  |  |

### Implementation Phase 6 — 运行、权限与审批的产品表达

| Goal | Description | Completed | Date |
|------|-------------|-----------|------|
| GOAL-007 | 让用户在执行前、执行中和执行后都能理解模式、预算、DAG、权限、审批和恢复状态。 |  |  |

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-027 | 在前端 Chat / 项目工作区新增运行面板，展示模式、预算、Tool DAG、逐 Step Outcome、部分完成原因和 terminal reason。 |  |  |
| TASK-028 | 将现有 `read_only/ask/full_workspace` 策略显示为可理解的运行前提示；展示受影响项目范围、待审批 Diff、Revision、审批过期时间和 Durable handoff 预览。 |  |  |
| TASK-029 | 为现有 Agent Runtime API 增加前端受控入口：查看 Run、Checkpoint、Artifact、DLQ、对账和已授权的 replay；不暴露 Worker owner、内部 lease 或敏感错误细节。 |  |  |
| TASK-030 | 使用前端 ESLint、生产构建和 Playwright 覆盖模式选择、部分成功、审批、Durable handoff 与恢复页面。 |  |  |

### Implementation Phase 7 — Skill / MCP 统一运行生命周期

| Goal | Description | Completed | Date |
|------|-------------|-----------|------|
| GOAL-008 | 将已审核 Skill 和 MCP 纳入同一份 ToolRunPolicy、质量合同、Budget、版本快照和失败治理体系，不建立开放市场。 |  |  |

| Task | Description | Completed | Date |
| TASK-031 | 让 Skill manifest 的 `durable_eligible`、allowed Tool、版本、digest、兼容性和审核状态成为运行前 Policy 校验输入；安装或升级不能静默改变已入队 Run。 |  |  |
| TASK-032 | 为 Skill / MCP 增加运行模式兼容性、Budget 声明、质量合同覆盖检查和失败可见性；MCP Schema / annotation 改变必须继续使审核失效。 |  |  |
| TASK-033 | 保持“系统推荐 Skill、用户确认激活”；不允许自动安装、未审核第三方执行代码、动态扩权或开放插件市场。 |  |  |

### Implementation Phase 8 — Gold Set 与运行质量评测

| Goal | Description | Completed | Date |
|------|-------------|-----------|------|
| GOAL-009 | 在执行语义稳定后建立可复现 Tool / Skill Gold Set，测量选择、参数、质量、拒绝、成功、延迟和预算收口。 |  |  |

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-034 | 固定覆盖检索、地图、工作区文件、Artifact、审批、依赖、并发、失败、恶意 evidence 和 Budget 用尽的 Tool / Skill Case。 |  |  |
| TASK-035 | 记录 Tool Recall@k、Skill Recall@k、Planner 选择正确率、参数正确率、ResultQuality 判定正确率、高风险拒绝正确率、任务成功率、部分成功正确率、P50/P95 延迟和 handoff 成功率。 |  |  |
| TASK-036 | 将评测配置、Tool/Skill manifest digest、模型配置和运行版本一起保存；明确区分离线评测结果与线上生产质量，不使用未稳定数据更新简历。 |  |  |

## 3. Alternatives

- **ALT-001**: 直接将 `ExternalContextService.max_agent_rounds` 从 5 改为 50 或移除。未选择，因为当前同步 HTTP 请求没有全局墙钟预算、跨 replan 调用账本、统一 Step Outcome 或 Chat-to-Durable handoff；放大轮次只会放大重复调用、成本、超时和不可恢复失败。
- **ALT-002**: 引入 LangGraph / Dify 并将现有代码重写为外部图框架。未选择，因为项目已有 AgentRun/Step/Checkpoint/Outbox/Lease/DLQ 基础；当前缺口是产品与同步链路未接通，而不是缺少图框架依赖。
- **ALT-003**: 只依赖 System Prompt 声明“Tool 输出不可信”。未选择，因为 `display_text` 仍会被投影到 Planner；需要代码侧的 Tool-specific projection、allowlist 和 Executor 复验。
- **ALT-004**: 允许 Planner 自行定义依赖是否可选、失败后是否重试。未选择，因为模型可能为了完成任务错误降低安全关键依赖或重复高风险操作；依赖强度、retry 与 handoff 必须由代码 Policy 决定。
- **ALT-005**: 先建立大量 Gold Set 与评测指标。未选择，因为当前执行状态与失败聚合尚不稳定，评测会锁定很快被推翻的行为口径。
- **ALT-006**: 借鉴 Codex 后开放 Shell、任意文件读写或主机全权限。未选择，因为本项目定位为个人/小团队本地知识工作平台，不是通用代码执行 Agent；这些能力与现有威胁模型不匹配。

## 4. Dependencies

- **DEP-001**: 当前同步 Tool 主链：`ExternalContextService`、`LLMToolPlanner`、`ToolWorkflowService`、`ToolExecutor`、`ContextPromptBuilder`。
- **DEP-002**: 当前持久化基础：`AgentRun`、`AgentStep`、`AgentCheckpoint`、`AgentArtifact`、`AgentOutboxEvent`、`DurableToolRunService`、`DurableToolWorker`。
- **DEP-003**: 当前安全基础：Tool Catalog、静态 manifest / 动态 MCP 审核、Credential Resolver、Project/Workspace ACL、Approval/Challenge/Revision CAS、SSRF 防护、敏感信息脱敏。
- **DEP-004**: 当前未提交的 `ToolWorkflowFeedback` 增量及其回归测试；阶段 1 必须在此基础上重构，不能将其回退为原始通用错误 observation。
- **DEP-005**: 公开调研结论：Codex 的多维预算与 Rollout 状态、LangGraph 的 Checkpointer/Interrupt、OpenAI Agents SDK 的 turn/concurrency/approval、Dify 的 Workflow/HITL 分离。它们用于设计原则，不作为必须引入的运行依赖。

## 5. Files

- **FILE-001**: `backend/app/services/tools/workflow.py` — 阶段 1 的 Step Outcome、依赖状态机、Run 聚合和调用账本主要实现位置。
- **FILE-002**: `backend/app/services/external_context_service.py` — 同步 Run 生命周期、replan observation、终止原因、Budget 和后续 Durable handoff 的入口。
- **FILE-003**: `backend/app/services/tools/quality.py` — 阶段 2 的通用质量合同、业务空结果语义和 Tool-specific evaluator 边界。
- **FILE-004**: `backend/app/tool_manifests/default_tools.json` — 默认 Tool 质量合同、结果语义和未来 Policy 元数据的声明位置。
- **FILE-005**: `backend/app/services/tools/executor.py` — 最终 Schema/ACL/凭据/风险/Approval 边界，以及 Tool 输出规范化与质量门入口。
- **FILE-006**: `backend/app/services/tools/planner.py` — 受限 Planner prompt、ToolRunPolicy / observation projection 输入；不得在此文件增加安全绕过。
- **FILE-007**: `backend/app/services/tools/formatter.py` 与 `backend/app/services/prompt_builder_service.py` — 最终 Answer evidence 的不可信边界和引用格式。
- **FILE-008**: `backend/app/services/durable_tool_runtime.py` 与 `backend/app/api/routes/agent_runtime.py` — 静态 DAG handoff、Run/Step/Checkpoint、重放与对账复用位置。
- **FILE-009**: `backend/app/models/agent_runtime.py` 与 `backend/app/schemas/agent_runtime.py` — 只有在 Durable handoff 需要额外持久字段时才迁移；阶段 1 不修改数据库 schema。
- **FILE-010**: `frontend/src/` 下 Chat、workspace 和 settings 组件 — 阶段 6 的运行面板、预算、权限、审批和 Durable Run 交互位置；阶段 0/1 不修改。
- **FILE-011**: `backend/tests/test_tool_workflow.py`、`backend/tests/test_tool_router.py`、`backend/tests/test_tool_executor.py`、`backend/tests/test_durable_tool_runtime.py` — 逐阶段回归测试位置。
- **FILE-012**: `docs/115_Tool执行状态语义与阶段0验收规范_2026-09-11.md` — 人类可读的阶段 0 架构基线。

## 6. Testing

- **TEST-001**: 阶段 0 文档一致性检查：状态表中的每个状态都可映射到现有同步或 Durable 代码；不存在将当前未实现能力表述为已实现的条目。
- **TEST-002**: 阶段 1 单测：同一 Run 内非可重试失败的相同调用指纹不得跨 replan 再次执行；不同规范化参数可重新规划。
- **TEST-003**: 阶段 1 单测：`succeeded + valid` 是严格依赖唯一解锁条件；`waiting_approval`、`invalid`、`uncertain`、`failed`、`blocked`、`timed_out` 均阻断严格下游。
- **TEST-004**: 阶段 2 单测：每个默认 Tool 验证业务有效、合法空结果、字段缺失、对象/作用域错配和质量失败 Prompt 抑制。
- **TEST-005**: 阶段 3 单测：并行三分支的单路失败不得取消两路成功；严格下游只受自身上游影响；最终 aggregate 为 `partial`。
- **TEST-006**: 阶段 4 安全回归：网页、文档和 MCP 返回中的恶意指令不能改变 allowlist、权限、审批、预算或产生未经授权的下一次 Tool Call。
- **TEST-007**: 阶段 5 集成测试：同步 Budget 耗尽时不再执行 Tool；确认 handoff 后只创建已验证的低风险只读 Durable Run；拒绝或超时不入队。
- **TEST-008**: 每个涉及后端实现的阶段执行 `PYTHONPATH=backend /disk2/gengnan/conda_envs/ai_web_studio/bin/python -m compileall -q backend/app backend/tests`、相关定向单测和完整后端回归；前端阶段额外执行 ESLint、生产构建与 Playwright 冒烟。

## 7. Risks & Assumptions

- **RISK-001**: 同步 Workflow 与 Durable Worker 当前状态词不完全一致；若阶段 1 直接修改持久化模型，可能破坏重放、Lease 和历史 Run 读取。缓解方式：阶段 1 仅增加同步 Outcome 适配层，不迁移 Durable 数据库字段。
- **RISK-002**: 过度禁止 Planner 读取 Tool evidence 会损害文件审阅和多步研究能力。缓解方式：阶段 4 采用按 Tool 类型的 projection 与受限 evidence excerpt，而不是简单删除所有内容。
- **RISK-003**: 去重规则如果忽略参数语义，可能阻止合法的查询修正。缓解方式：仅对 Tool Key + canonical arguments 完全相同、且终态不可重试的调用指纹阻断；不同参数、显式用户新输入和 Durable 重放按独立策略处理。
- **RISK-004**: 允许过多并发会放大 Provider 限流、成本和 Tool 侧副作用。缓解方式：并发预算必须在阶段 5 成为代码侧 Policy，且 Durable 继续只允许低风险只读 Tool。
- **RISK-005**: Chat handoff 若不绑定 Plan、Skill digest、项目范围和预算快照，可能发生“前台看到的计划”和“后台执行的计划”不一致。缓解方式：阶段 5 将这些值作为 handoff 请求哈希的一部分。
- **ASSUMPTION-001**: 现有 `ToolExecutor` 的最终 Schema/ACL/凭据/SSRF/风险边界继续保留，后续重构只调用它，不复制或绕过其逻辑。
- **ASSUMPTION-002**: 当前产品定位保持为个人/小团队的本地知识工作平台；不以通用代码代理或开放插件市场为近期目标。
- **ASSUMPTION-003**: 评测在阶段 8 前只维护现有回归样例，不将尚未稳定的运行行为写入简历或对外性能指标。

## 8. Related Specifications / Further Reading

- [阶段 0 人类可读规范](../docs/115_Tool执行状态语义与阶段0验收规范_2026-09-11.md)
- [当前项目实现全景与面试口径](../docs/100_项目当前实现全景与面试口径_2026-08-03.md)
- [本地服务恢复检查与 Tool 质量重规划](../docs/114_本地服务恢复检查与Tool质量重规划_2026-09-11.md)
- [OpenAI Codex public repository](https://github.com/openai/codex)
- [LangGraph public repository](https://github.com/langchain-ai/langgraph)
- [OpenAI Agents SDK public repository](https://github.com/openai/openai-agents-python)
- [Dify public repository](https://github.com/langgenius/dify)
