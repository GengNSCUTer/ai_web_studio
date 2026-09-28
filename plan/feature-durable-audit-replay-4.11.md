---
goal: 阶段 4.11 Durable 执行审计导出与安全回放
version: 1.0
date_created: 2026-09-28
last_updated: 2026-09-28
owner: AI Web Studio
status: 'Completed'
tags: [feature, durable-agent, audit, replay, security]
---

# Introduction

在 4.9 的任务管理和 4.10 的执行方式建议之上，补齐 Durable Run 的可审计执行记录与安全回放边界。审计只导出状态、版本摘要和哈希，不导出原始参数、凭据、远端正文或完整产物；回放必须重新检查当前工具和 Skill 的版本、审核状态与只读低风险约束。

## 1. Requirements & Constraints

- **REQ-001**: 每个新 Durable Run 持久化工具执行快照摘要，包括工具身份、只读/风险属性和稳定指纹；Skill 保存版本与 manifest digest。
- **REQ-002**: 提供用户作用域的 JSONL 审计导出，覆盖 Run、Step、Checkpoint、Artifact 和 Outbox 的关键状态，不暴露原始 arguments、凭据、远端正文或完整 content_json。
- **REQ-003**: 受控 replay 在入队前重新校验工具指纹和 Skill 版本；当前定义漂移、审核状态失效或风险升级时拒绝回放。
- **REQ-004**: 历史缺少快照的旧 Run 不被静默伪装成已完成审计；允许兼容查看并在审计中标记 legacy_snapshot，回放仍执行既有低风险/allowlist 检查。
- **REQ-005**: 任务页提供审计下载入口；正常 Chat、文件写入和高风险工具行为不改变。
- **SEC-001**: 所有查询和下载按当前用户隔离；审计字段使用哈希、计数和受控枚举，默认不输出模型参数和第三方响应。

## 2. Implementation Steps

### Implementation Phase 1

- GOAL-001: 建立稳定工具快照和漂移检查。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | 为 ToolDefinition 增加不含凭据的执行身份摘要与稳定指纹。 | 是 | 2026-09-28 |
| TASK-002 | enqueue 保存工具快照；Worker 和 replay 重新验证工具/Skill 快照。 | 是 | 2026-09-28 |

### Implementation Phase 2

- GOAL-002: 完成审计导出和用户界面闭环。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-003 | 新增用户作用域 JSONL 审计服务和下载 API。 | 是 | 2026-09-28 |
| TASK-004 | 任务页显示并下载当前 Run 的审计 JSONL。 | 是 | 2026-09-28 |
| TASK-005 | 完成 API、回放漂移、脱敏、浏览器和 PostgreSQL 全量回归。 | 是 | 2026-09-28 |

## 3. Alternatives

- **ALT-001**: 直接导出 `arguments_json` 和 `content_json`；放弃该方案，避免审计文件成为凭据、用户文档或外部恶意正文的泄露出口。
- **ALT-002**: 回放时总是使用当前 Tool 定义；放弃该方案，避免版本漂移后重复执行语义已经变化的任务。

## 4. Non-goals

- 不增加 Shell、Bash、SQL、删除文件、外部发布、支付、邮件或任意 HTTP 写入。
- 不引入外部日志平台、邮件/短信告警和自动扩容；这些属于部署层，不是本地工作台核心链路。

## 5. Verification

- 审计 JSONL 每行是稳定 JSON 对象，包含事件类型、时间、Run/Step 标识和受控摘要。
- 伪造工具版本、风险等级升级、Skill digest 变化时 replay 失败关闭。
- 不同用户无法读取、下载或重放他人的 Run。
- 真实 Worker、API、任务页和浏览器下载路径通过；后端全量 PostgreSQL 测试通过。

## 6. Completion

阶段 4.11 已完成。后端 PostgreSQL 全量回归 `491 tests OK`；Durable 定向回归 35 项通过；前端 ESLint、`npm run build`、Python `compileall` 和 `git diff --check` 通过。真实 Worker + 任务页 + 原会话回写 + 审计 JSONL 浏览器烟测通过，临时任务和数据库记录已清理。
