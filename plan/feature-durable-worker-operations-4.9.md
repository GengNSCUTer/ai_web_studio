---
goal: 阶段 4.9 Durable Worker 运行保障与任务管理
version: 1.0
date_created: 2026-09-28
last_updated: 2026-09-28
owner: AI Web Studio
status: 'Completed'
tags: [feature, durable-agent, operations]
---

# Introduction

![Status: Completed](https://img.shields.io/badge/status-Completed-brightgreen)

在现有 Outbox、Lease、Checkpoint 和 DLQ 上补充进程可观测性、优雅停机与用户可见的任务管理，不扩大工具权限。

## 1. Requirements & Constraints

- **REQ-001**: Worker 启动时检查数据库，运行中持续登记心跳，停机时停止领取新任务并登记退出。
- **REQ-002**: 在线状态依赖持久化心跳及超时阈值，进程意外退出后必须自动显示为离线。
- **REQ-003**: 用户可查看自己的 Durable Run、Step、Artifact 和 DLQ，并显式重放自己权限范围内的 dead-letter Step。
- **REQ-004**: 重放成功后原会话结果消息更新为最新终态，不产生重复消息。
- **SEC-001**: 列表、详情和重放必须按当前用户过滤；Worker 全局视图只暴露聚合状态。
- **CON-001**: 保留当前只读低风险 Tool allowlist 与已有 Lease/Fencing/Retry/DLQ 状态机。

## 2. Implementation Steps

### Implementation Phase 1

- GOAL-001: 完成 Worker 生命周期与告警。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | 在 `backend/app/models/agent_runtime.py` 新增 Worker 心跳表；在独立服务中实现注册、心跳、停机和超时聚合。 | 是 | 2026-09-28 |
| TASK-002 | 在 `backend/app/services/durable_tool_runtime.py` 和启动脚本接入唯一 Worker 身份、启动检查、运行心跳及 SIGTERM 优雅停机。 | 是 | 2026-09-28 |
| TASK-003 | 在用户作用域指标中增加无 Worker、过期租约、超时排队告警。 | 是 | 2026-09-28 |

### Implementation Phase 2

- GOAL-002: 完成任务管理和收口验证。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-004 | 增加用户作用域 Run 列表和 Worker 聚合健康接口，并在 `frontend/src/app/tasks/` 展示 Run/Step/Artifact/DLQ 与受控重放。 | 是 | 2026-09-28 |
| TASK-005 | 修复重放后原会话终态投影，覆盖成功、失败、重复重放及权限测试。 | 是 | 2026-09-28 |
| TASK-006 | 运行后端、前端、真实 Worker 和 Playwright 浏览器检查，更新阶段文档。 | 是 | 2026-09-28 |

## 3. Alternatives

- **ALT-001**: 引入额外 Broker 或监控平台；本阶段已有 PostgreSQL 状态机足以承载小规模部署，不引入额外组件。

## 4. Dependencies

- **DEP-001**: 当前 `AgentRun`、`AgentStep`、`AgentOutboxEvent`、Worker Lease 与 Replay API。

## 5. Files

- **FILE-001**: `backend/app/models/agent_runtime.py` 存储 Worker 心跳。
- **FILE-002**: `backend/app/services/durable_worker_registry.py` Worker 状态和健康聚合。
- **FILE-003**: `backend/app/services/durable_tool_runtime.py` Worker 运行和结果投影。
- **FILE-004**: `backend/app/api/routes/agent_runtime.py` 用户任务和健康接口。
- **FILE-005**: `frontend/src/app/tasks/` 用户任务页。

## 6. Testing

- **TEST-001**: 同名在线 Worker 不得重复启动，过期实例可接管，旧实例心跳不得覆盖新实例。
- **TEST-002**: 停机不领取新任务，意外退出后心跳超时显示离线。
- **TEST-003**: 用户无法查看或重放他人的 Run；重复点击重放不会创建两个新事件。
- **TEST-004**: 重放成功只更新原会话的一条结果消息。
- **TEST-005**: 实际 Worker、API 与浏览器页面验证通过。

## 7. Risks & Assumptions

- **RISK-001**: 进程被强制杀死时无法运行停机回调；心跳超时与现有 Lease 接管负责恢复。
- **ASSUMPTION-001**: Worker 心跳记录只面向部署观察，不把全局任务内容暴露给普通用户。

## 8. Related Specifications / Further Reading

- `docs/154_阶段4.8_Durable结果回写与会话收口_2026-09-22.md`
- `docs/155_阶段4.9_DurableWorker运行保障与任务管理_2026-09-28.md`
