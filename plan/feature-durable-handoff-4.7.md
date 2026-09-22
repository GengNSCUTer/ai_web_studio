---
goal: 显式 Chat 到 Durable Agent Run 的安全交接
version: 1.0
date_created: 2026-09-22
last_updated: 2026-09-22
owner: AI Web Studio
status: 'Complete'
tags: [feature, durable-agent, handoff, security]
---

# Introduction

![Status: Complete](https://img.shields.io/badge/status-Complete-brightgreen)

本阶段把适合长时间运行的低风险只读 Chat 任务显式交接到已有 Durable Runtime。普通同步 Chat 保持有界执行；只有用户确认后，系统才创建可恢复的 AgentRun。

## 1. Requirements & Constraints

- **REQ-001**: 普通 Chat 不得自动进入 Durable Runtime，必须由用户显式发起并确认。
- **REQ-002**: handoff 只允许已审核、已启用且 `durable_eligible=true` 的 Skill。
- **REQ-003**: handoff 只允许低风险只读 Tool，复用 DurableToolRunService 的 Schema、权限、依赖和幂等校验。
- **SEC-001**: 不能因为 handoff 扩大当前用户、项目、会话或 Tool allowlist 范围。
- **SEC-002**: 高风险写入、任意文件路径、Shell、SQL、支付、邮件和外部发布不进入本阶段。
- **CON-001**: 复用现有 AgentRun、AgentStep、AgentOutboxEvent、AgentCheckpoint、Lease/Fencing 和 DLQ，不另建状态机。
- **GUD-001**: API 返回机器可读状态和安全摘要，不回显凭据、原始 Provider 响应或完整敏感参数。

## 2. Implementation Steps

### Implementation Phase 1

- GOAL-001: 增加可审计的 Durable handoff 预览和确认入口。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | 定义 handoff 请求、预览和确认响应模型，绑定 user/project/conversation/assistant_message 作用域。 | 是 | 2026-09-22 |
| TASK-002 | 使用已审核 Skill 和只读 Tool 计划生成预览；预览阶段不创建可执行 Outbox。 | 是 | 2026-09-22 |
| TASK-003 | 确认接口复用 DurableToolRunService.enqueue()，以 idempotency key 原子创建 Run/Step/Outbox/Checkpoint。 | 是 | 2026-09-22 |

### Implementation Phase 2

- GOAL-002: 验证交接状态、Worker 执行和前端可观察性。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-004 | 增加 API 和服务层状态收口，防止重复确认、过期预览和请求哈希不一致。 | 是 | 2026-09-22 |
| TASK-005 | 增加定向测试：权限边界、Skill durable eligibility、低风险 Tool、幂等、确认前无 Run、确认后可恢复。 | 是 | 2026-09-22 |
| TASK-006 | 增加真实低风险 Durable Worker 烟测，并记录 4.7 阶段文档。 | 是 | 2026-09-22 |

## 3. Alternatives

- **ALT-001**: 让 Chat 在达到轮次上限时静默进入后台；拒绝，因为用户无法感知权限和成本变化。
- **ALT-002**: 另建独立队列状态表；拒绝，因为当前 Durable Runtime 已有完整 Run/Step/Checkpoint/Lease/DLQ 能力。

## 4. Dependencies

- **DEP-001**: `backend/app/services/durable_tool_runtime.py` 的低风险只读验证和幂等入队。
- **DEP-002**: `backend/app/services/skill_catalog.py` 的 Skill 版本锁和 durable eligibility。
- **DEP-003**: PostgreSQL 运行时表和现有 Durable Worker。

## 5. Files

- **FILE-001**: `backend/app/api/routes/agent_runtime.py` handoff API。
- **FILE-002**: `backend/app/schemas/agent_runtime.py` handoff 数据模型。
- **FILE-003**: `backend/app/services/durable_handoff_service.py` 预览、确认和状态校验。
- **FILE-004**: `backend/tests/test_durable_handoff_service.py` handoff 测试。
- **FILE-005**: `docs/153_阶段4.7_显式DurableHandoff_2026-09-22.md` 实现记录。

## 6. Testing

- **TEST-001**: 未确认预览不创建 AgentRun、AgentStep 或 OutboxEvent。
- **TEST-002**: 确认只允许 durable Skill 和 low-risk/read-only Tool。
- **TEST-003**: 相同幂等键和请求哈希返回同一 Run，不同请求哈希明确冲突。
- **TEST-004**: 确认后 Worker 可领取 Step 并写入 Artifact/Checkpoint。
- **TEST-005**: 跨用户、跨项目和高风险 Tool 均被拒绝。

真实在线模型浏览器 smoke 已验证同步 Chat 的 Planner/Tool/Answer 主链和前端状态面板；Worker 仍由独立脚本按部署环境运行，本阶段没有把它伪装成常驻服务。

## 7. Risks & Assumptions

- **RISK-001**: LLM 生成的计划可能不适合长任务；预览和代码侧 allowlist 必须共同约束。
- **RISK-002**: Durable Worker 目前通过数据库 Outbox 直接领取，后续可再替换为专用 Broker，不改变 API 合同。
- **ASSUMPTION-001**: 第一批 handoff 仅接入现有低风险只读 Tool，不包含文件写入 Skill。

## 8. Related Specifications / Further Reading

- `docs/90_DurableAgent运行时与Artifact_Skill演进_2026-07-31.md`
- `docs/126_阶段5.1_工具运行模式与预算基础_2026-09-19.md`
- `backend/app/services/durable_tool_runtime.py`
