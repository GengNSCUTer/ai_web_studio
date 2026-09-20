---
goal: 将同步 Tool Chat 从单一五轮限制升级为显式运行模式与多维执行预算
version: 1.0
date_created: 2026-09-19
last_updated: 2026-09-19
owner: AI Web Studio
status: 'In progress'
tags: [architecture, agent, tool-workflow, budget, durable-run]
---

# Introduction

![Status: In progress](https://img.shields.io/badge/status-In%20progress-yellow)

当前同步 Chat 固定最多五轮 `plan -> execute -> observe`，但没有统一限制总调用数、重规划次数、并发度、工具阶段墙钟时间或证据量。此计划用代码侧 `ToolRunPolicy + ToolRunBudget` 取代单一轮次常量：短问答保持快速可控，研究类任务可在更大的有限预算中运行；真正超出同步预算的任务仍需用户可见地转入现有 Durable Runtime。

## 1. Requirements & Constraints

- **REQ-001**: 运行模式必须是代码侧有限枚举：`quick_chat`、`guided_research`、`workspace_review`、`edit_proposal`、`durable_task`；未知模式必须失败关闭为默认同步模式或由 API 校验拒绝。
- **REQ-002**: 每个同步模式必须同时限制规划轮次、总 Tool 调用数、单计划调用数、并发度、重规划次数、工具阶段墙钟时间和可注入 evidence 字符数。
- **REQ-003**: 默认 `quick_chat` 必须保持当前五轮兼容口径；`guided_research` 才能获得高于五轮的有限预算。
- **REQ-004**: 同步请求耗尽任一预算时，只允许 `finalize_partial`、`clarify` 或 `stop`；不得由 Planner 静默增加预算或自动转入后台。
- **SEC-001**: Tool Executor 继续是 Schema、ACL、凭据、SSRF、风险和审批的最终边界；运行模式不能放宽 Tool 权限。
- **SEC-002**: `durable_task` 不是同步 Chat 的可选无限模式；只有用户确认后的静态低风险只读 DAG 才能使用现有 Durable API。
- **CON-001**: 本阶段不新增 Bash、Shell、SQL、删除、支付、邮件、外部发布、任意 HTTP 写入或任意本机文件写入。
- **CON-002**: 当前 Durable Runtime 不支持持久化 Planner continuation；本计划不得把多轮同步 Planner 误称为可恢复的长程 Agent。
- **GUD-001**: 每次只落一个可验证能力点，先完成定向测试、完整后端回归、编译检查和文档同步。
- **PAT-001**: 使用不可变 Policy 与请求作用域 Budget State；Planner 只能观察剩余预算的脱敏摘要，不能修改其数值。

## 2. Implementation Steps

### Implementation Phase 1 — 策略与预算基础设施

| Goal | Description | Completed | Date |
|------|-------------|-----------|------|
| GOAL-001 | 建立模式枚举和统一 Budget State，不改变默认 quick_chat 的五轮行为。 | ✅ | 2026-09-19 |

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | 新建 `backend/app/services/tools/run_policy.py`，定义不可变 `ToolRunPolicy`、`ToolRunBudget`、模式常量和 resolver；固定 quick_chat=5 轮、guided_research=8 轮等明确配置。 | ✅ | 2026-09-19 |
| TASK-002 | 修改 `ToolWorkflowService.run()`，接收受控的单计划调用上限与并发上限；超过预算的调用生成现有安全 Outcome，不进入 Executor。 | ✅ | 2026-09-19 |
| TASK-003 | 修改 `ExternalContextService.build_context()`，按 Budget 记录轮次、实际尝试调用数和重规划次数，在耗尽时生成稳定 terminal reason 与脱敏提示。 | ✅ | 2026-09-19 |
| TASK-004 | 增加 `test_tool_run_policy.py` 和 `test_tool_router.py` 用例，覆盖默认兼容、研究模式八轮、总调用耗尽、重规划耗尽、并发限额和无效模式。 | ✅ | 2026-09-19 |

### Implementation Phase 2 — 用户显式模式选择与可观测性

| Goal | Description | Completed | Date |
|------|-------------|-----------|------|
| GOAL-002 | 让用户在发起 Chat 前选择受控模式，并在 Trace/上下文面板中理解实际预算。 | ✅ | 2026-09-19 |

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-005 | 在 `backend/app/schemas/message.py`、Chat route 和 Next 代理中增加 `tool_run_mode`；新建、重生成、编辑重答均使用同一受限枚举。 | ✅ | 2026-09-19 |
| TASK-006 | 在 `frontend/src/components/chat-composer.tsx` 和 `chat-thread.tsx` 增加模式选择与预算展示；默认 quick_chat，不因 Skill 自动扩大预算。 | ✅ | 2026-09-19 |
| TASK-007 | 在 diagnostics/details 中暴露模式、已用/剩余预算和终止原因；不得暴露 Provider 原始错误、凭据或调用参数。 | ✅ | 2026-09-19 |
| TASK-008 | 执行前端 ESLint、生产构建和 Playwright 冒烟，覆盖模式选择、重生成和预算耗尽提示。 | ✅ | 2026-09-19 |

### Implementation Phase 3 — 显式 Durable handoff

| Goal | Description | Completed | Date |
|------|-------------|-----------|------|
| GOAL-003 | 同步预算不足时提供用户确认的静态只读 DAG handoff，而非扩展 HTTP 内循环。 |  |  |

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-009 | 复用 `DurableToolRunService.enqueue()`，将已通过 Catalog/Scope/Skill digest 校验的静态低风险只读 DAG 作为 handoff proposal 持久化绑定 source Chat、Plan 和 Budget snapshot。 |  |  |
| TASK-010 | 用户确认后才调用 Durable enqueue；拒绝、过期或预算快照不匹配时不入队。 |  |  |
| TASK-011 | 覆盖 handoff 的审批、请求哈希、Skill digest、范围漂移、DLQ 和恢复快照测试。 |  |  |

## 3. Alternatives

- **ALT-001**: 直接把 `max_agent_rounds` 改为 20 或移除。未选择，因为这只放大重复调用、Provider 限流、超时和不可恢复失败，仍没有总预算或用户可见的运行模式。
- **ALT-002**: 所有任务都改为 Durable Runtime。未选择，因为普通问答的延迟和交互成本会显著增加，且当前 Durable Runtime 只支持静态低风险只读 DAG。
- **ALT-003**: 由 Planner 根据任务难度自行选择轮数。未选择，因为模型输出不是权限或成本授权，且易被 Tool 输出注入影响。
- **ALT-004**: Skill 被选择后自动提升到研究模式。未选择，因为 Skill 选择不等于用户同意更高成本或更长运行；模式必须用户显式选择。

## 4. Dependencies

- **DEP-001**: `backend/app/services/external_context_service.py` 的同步 observe/re-plan 循环和阶段 3.3 动作收口。
- **DEP-002**: `backend/app/services/tools/workflow.py` 的 Step Outcome、aggregate、质量门、调用账本和 DAG 并发执行。
- **DEP-003**: `backend/app/services/durable_tool_runtime.py` 既有低风险只读 Durable Run、Checkpoint、Lease、DLQ 和 replay 能力。
- **DEP-004**: `backend/app/schemas/message.py`、Chat route 与 Next 代理的请求契约。

## 5. Files

- **FILE-001**: `backend/app/services/tools/run_policy.py` — 新的模式定义、固定 Policy 和请求作用域 Budget。
- **FILE-002**: `backend/app/services/tools/workflow.py` — 单计划调用/并发预算执行边界。
- **FILE-003**: `backend/app/services/external_context_service.py` — Budget 消耗、终止动作、Trace 和 diagnostics。
- **FILE-004**: `backend/app/schemas/message.py`、`backend/app/services/chat_execution_service.py`、`backend/app/services/chat_context_assembly_service.py` — Phase 2 的模式请求传递。
- **FILE-005**: `frontend/src/components/chat-composer.tsx`、`frontend/src/components/chat-thread.tsx`、`frontend/src/app/api/chat/route.ts` — Phase 2 的显式选择与请求代理。
- **FILE-006**: `backend/tests/test_tool_run_policy.py`、`backend/tests/test_tool_workflow.py`、`backend/tests/test_tool_router.py` — Policy、Budget 和端到端回归。

## 6. Testing

- **TEST-001**: quick_chat 保持五轮上限且总调用、重规划、并发和墙钟预算均可观测。
- **TEST-002**: guided_research 仅在显式选中时允许八轮；总调用数达到上限后不进入 Executor。
- **TEST-003**: 重规划或墙钟预算耗尽时，具有有效证据则 `finalize_partial`，否则 `stop/clarify`；不能继续隐式扩轮。
- **TEST-004**: 同一 ready 批次的实际 Executor 并发量不超过模式 Policy。
- **TEST-005**: 每一后端阶段执行相关单测、完整后端回归、`compileall` 和 `git diff --check`；前端阶段另执行 ESLint、build 与 Playwright。

## 7. Risks & Assumptions

- **RISK-001**: 同步 HTTP 工具阶段的墙钟超时只能取消正在等待的协程，不能把第三方已经发送的请求变成 Exactly Once。缓解方式是保留幂等键、质量门和 Durable handoff。
- **RISK-002**: Budget 过紧会损害复杂研究体验，过宽会带来成本和延迟。缓解方式是固定少数模式、记录使用量，并在 Gold Set 稳定后再调参。
- **RISK-003**: 前端未接入前，后端模式参数只可由 API 使用。缓解方式是 Phase 1 保持默认兼容，Phase 2 才公开用户选择。
- **ASSUMPTION-001**: 默认 quick_chat 的五轮上限是当前兼容基线；提升只能通过显式 guided_research 模式发生。
- **ASSUMPTION-002**: 现有 Executor 和 Durable Runtime 的安全边界在本计划中保持不变。

## 8. Related Specifications / Further Reading

## 9. Phase 2 Verification

- Chat 请求、重生成和编辑重答均接受同一受限 `tool_run_mode`，缺省为 `quick_chat`；`durable_task` 不会从同步接口进入。
- Composer 增加用户可见的运行模式选择器：快速对话、研究模式、工作区审阅、编辑提案。生成期间和编辑期间禁用切换，Skill 不会静默提升预算。
- 上下文诊断面板显示实际模式、规划轮数、工具调用数、剩余重规划次数和剩余工具阶段时间；头部摘要不携带工具参数、Provider 正文或凭据。
- 验证结果：相关后端单测 `28 tests OK`；`compileall` 通过；变更文件 ESLint 通过；Next 生产构建通过；Playwright 登录后选择器冒烟通过，确认默认 `quick_chat` 可切换到 `guided_research`，浏览器 console/request failure 均为空。

- [统一 Tool 执行状态计划](design-tool-execution-state-semantics-1.md)
- [阶段 3.3 工具结果收口策略](../docs/125_阶段3.3_工具结果收口策略_2026-09-17.md)
- [当前项目实现全景与面试口径](../docs/100_项目当前实现全景与面试口径_2026-08-03.md)
