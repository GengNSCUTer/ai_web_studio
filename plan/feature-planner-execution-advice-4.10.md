---
goal: 阶段 4.10 Planner 同步与后台候选建议
version: 1.0
date_created: 2026-09-28
last_updated: 2026-09-28
owner: AI Web Studio
status: 'Completed'
tags: [feature, planner, durable-agent]
---

# Introduction

![Status: Completed](https://img.shields.io/badge/status-Completed-brightgreen)

Planner 可以给出执行方式建议，但普通 Chat 继续同步执行。用户确认后台任务时会重新执行同一个只读计划；前端必须明确这一点。建议不等于权限、入队或审批。

## 1. Requirements & Constraints

- **REQ-001**: Planner 输出 `sync` 或 `durable_candidate` 及简短原因；旧模型输出和确定性回退默认为 `sync`。
- **REQ-002**: 只有多步骤、低风险只读、Skill 允许 Durable 的计划才能保留后台候选建议。
- **REQ-003**: 前端区分建议与实际执行，明确普通 Chat 已同步执行，后台确认将重新执行只读步骤。
- **SEC-001**: 建议不能自动创建 Run，不能绕过预览、用户确认与服务端权限复核。
- **CON-001**: 不改变现有 Tool Workflow、Durable 状态机与五轮同步预算。

## 2. Implementation Steps

### Implementation Phase 1

- GOAL-001: 建立可兼容的 Planner 建议合同及代码侧安全约束。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | 扩展 ToolPlan 和 Planner JSON 提示、解析与安全降级。 | 是 | 2026-09-28 |
| TASK-002 | 覆盖模型建议、旧输出、回退、风险工具和 Skill 边界测试。 | 是 | 2026-09-28 |

### Implementation Phase 2

- GOAL-002: 用户可见且不会误认后台任务自动启动。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-003 | Tool Trace 显示建议及重执行说明，保留预览/确认入口。 | 是 | 2026-09-28 |
| TASK-004 | 执行后端、前端、浏览器和真实在线模型验证，并更新文档。 | 是 | 2026-09-28 |

## 3. Alternatives

- **ALT-001**: 模型建议后立即中断同步 Chat；这需要新的用户决策及恢复协议，当前阶段不引入，以免正常 Chat 被模型建议阻断。

## 4. Dependencies

- **DEP-001**: ToolPlan、ToolCatalog、SkillExecutionContext、DurableHandoffService。

## 5. Files

- **FILE-001**: `backend/app/services/tools/planner.py` 输出解析和建议校验。
- **FILE-002**: `backend/app/services/tools/schemas.py` 建议字段与公开投影。
- **FILE-003**: `frontend/src/components/tool-trace-panel.tsx` 用户提示与确认入口。
- **FILE-004**: `frontend/src/lib/types.ts` Trace 类型。

## 6. Testing

- **TEST-001**: 多只读工具候选保留建议；单工具、高风险、非 Durable Skill 均降级同步。
- **TEST-002**: 缺少新字段的模型输出和确定性回退继续同步。
- **TEST-003**: 预览前无 Run；确认后仍经服务端校验；浏览器显示重执行说明。

## 7. Risks & Assumptions

- **RISK-001**: 同一个只读计划可能被用户明确选择再执行一次；前端必须说明，不能用于写入类计划。
- **ASSUMPTION-001**: 当前阶段的建议仅改善发现入口，不负责实际执行时长预测。

## 8. Related Specifications / Further Reading

- `plan/feature-durable-handoff-4.7.md`
- `plan/feature-durable-worker-operations-4.9.md`
