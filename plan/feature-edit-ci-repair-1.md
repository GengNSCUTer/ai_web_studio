---
goal: 修复后端CI可选依赖、明确替换的规划超时与编辑状态虚报
version: 1.0
date_created: 2026-11-05
last_updated: 2026-11-05
owner: AI Web Studio
status: In progress
tags: [bug, ci, tools, approval]
---

# Introduction

![Status: In progress](https://img.shields.io/badge/status-In_progress-yellow)

用户已授权修复。本轮针对200题揭示的编辑链与CI，不改原题、不增加预算或权限。保留原批次，另跑明确标记的审批子集。

## 1. Requirements & Constraints

- **REQ-001**: 无Playwright的后端环境仍可导入纯评测函数并运行单测；真实浏览器入口保留显式依赖。
- **REQ-002**: 单目标、明确引号内old/new使用代码规划；复杂单文件只由代码先定位/读取，实际原文到齐后仍由LLM生成编辑参数。不猜替换。
- **REQ-003**: edit_proposal中的预览/审批请求由实际规范结果生成回复，缺审批不得宣称创建成功；保留流式事件、generation保护和持久化。
- **SEC-001**: 候选过滤、Skill allowlist、原任务检查、Executor、用户确认、Revision CAS不变；代码规划不等于自动写入。
- **CON-001**: 不加时长/轮数，不修改在线Provider，不启动本地模型，不覆盖原200题结果。

## 2. Implementation Steps

### Implementation Phase 1

- **GOAL-001**: 修复GitHub Actions导入失败并验证无浏览器环境。

| Task | Description | Completed | Date |
|---|---|---|---|
| TASK-001 | scripts/agent_task_approval_runner.py将Playwright移到真实浏览器分支；test_agent_200_tasks.py用独立解释器禁止该包验证导入 | 是 | 2026-11-05 |

### Implementation Phase 2

- **GOAL-002**: 明确替换在原预算内产生真实Diff/审批，输出真实状态。

| Task | Description | Completed | Date |
|---|---|---|---|
| TASK-002 | tools/literal_file_edit.py解析明确替换并按观察生成search→read→propose/apply，复杂编辑只前移定位读取；planner.py不绕过候选 | 是 | 2026-11-05 |
| TASK-003 | file_edit_response.py从规范来源/最终映射生成预览、待审批、已应用或未完成；Chat复用原流式收口 | 是 | 2026-11-05 |

### Implementation Phase 3

- **GOAL-003**: 单测与真实在线/浏览器验证后更新文档、飞书、提交并确认CI。

| Task | Description | Completed | Date |
|---|---|---|---|
| TASK-004 | 1045项含PG通过，原20题四批各20/20；在线5题严格3/5（参数部分遵从保留），显式确认1/1 | 是 | 2026-11-05 |
| TASK-005 | docs/206和04—07、计划、飞书入口更新；提交推送并检查新Actions实际结果 | 否 | 2026-11-05 |

## 3. Alternatives

- **ALT-001**: 后端requirements直接安装浏览器依赖；不采用，纯分析不应强依赖端到端环境。
- **ALT-002**: 仅增加预算或提示词；不采用，已有多轮规划与虚报证据，不能靠提示词保证状态真实。

## 4. Dependencies

- **DEP-001**: 现有Workspace Tools、ToolPlan、只读观察、审批与Chat NDJSON机制。
- **DEP-002**: 已有在线DeepSeek-V3.2、PostgreSQL、前后端、Playwright及GitHub CLI授权。

## 5. Files

- **FILE-001**: scripts/agent_task_approval_runner.py、backend/tests/test_agent_200_tasks.py：可选依赖隔离。
- **FILE-002**: backend/app/services/tools/literal_file_edit.py、planner.py：明确替换代码规划。
- **FILE-003**: backend/app/services/file_edit_response.py、chat_execution_models.py、chat_context_assembly_service.py、api/routes/chat.py：真实状态回复。

## 6. Testing

- **TEST-001**: 用阻止Playwright导入的子进程验证纯单测依赖。
- **TEST-002**: 无候选、无原文、疑似指令正文、多文件、歧义/缺文本不能生成猜测写入；生成的调用仍过Schema/Executor。
- **TEST-003**: 不请求最终模型也可NDJSON输出、持久化done及实际审批卡；取消/版本冲突/重复确认不回退安全策略。
- **TEST-004**: 原20编辑题真实执行与失败分支保留，报告与原200题分开；GitHub Actions必须读取新提交结果。

## 7. Risks & Assumptions

- **RISK-001**: 代码规划只覆盖明确替换，不冒称自然语言编辑全部无需模型。
- **RISK-002**: 编辑模式只报告动作状态，不再用最终模型扩写；普通Chat/研究模式保持原回答能力。
- **ASSUMPTION-001**: 真实修改对象为ProjectFile规范正文和FileRevision，不是宿主任意路径或覆盖原上传文件。

## 8. Related Specifications / Further Reading

[205首轮200题](../docs/205_Agent工具路由修复与200题业务回归_2026-11-05.md)、[原评测计划](feature-agent-task-evaluation-1.md)。
