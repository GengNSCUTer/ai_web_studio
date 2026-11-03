---
goal: 长期记忆分层冲突分析与版本化人工确认
version: 1
date_created: 2026-11-03
last_updated: 2026-11-03
owner: AI Web Studio
status: Completed
tags: [memory, safety, semantic]
---

# Introduction

![Status: Completed](https://img.shields.io/badge/status-Completed-brightgreen)

事实键和精确范围找候选，Embedding补充同义记忆，结构化模型判断重复/矛盾/补充/不同条件；数据库短事务最终检查并替换。

## 1. Requirements & Constraints

- **REQ-001**: 只比较当前用户、同项目/全局范围内有效active记忆，不外发其他项目或敏感原文。
- **REQ-002**: 精确键优先，向量/词法补候选；相似度不等于矛盾，模型输出不能自选ID或激活。
- **REQ-003**: 关系包含duplicate/contradiction/complement/different_scope/uncertain；置信度低于0.85、高影响、分析失败/覆盖不足全部待审。
- **REQ-004**: 原说明与相关active版本绑定；确认时检查候选、范围内版本和明确替换目标版本，同事务激活/旧版superseded。
- **SEC-001**: 不能执行记忆内容中的指令，不静默替换；人工创建、编辑、建议和Worker都使用同一分析服务。
- **CON-001**: 复用现有在线Provider/记忆Embedding缓存、用户锁、版本和审批入口；不加载Ollama、不新增ANN或记忆Agent框架。

## 2. Implementation Steps

### Implementation Phase 1

- **GOAL-001**: 实现分析与持久化（先完成TASK-001，再TASK-002）。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | 新增memory_conflict_service.py、memory_conflict_policy.py和UserMemory.conflict_review_json；20261103_04迁移。 | 是 | 2026-11-03 |
| TASK-002 | Worker在锁外分析，锁内复核；MemoryService校验分析快照、明确替换和版本，失效分析留待复查。 | 是 | 2026-11-03 |

### Implementation Phase 2

- **GOAL-002**: 完成API、界面与验收，依赖Phase1。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-003 | memories.py接入创建/建议/重新分析/编辑；审核页显示关系和旧值，发送候选/目标版本与明确确认。 | 是 | 2026-11-03 |
| TASK-004 | 补语义关系、降级、跨用户/项目、并发和版本集成测试；真实在线模型+浏览器审核，执行全量回归。 | 是 | 2026-11-03 |
| TASK-005 | 更新docs/199、当前项目和学习入口，飞书同步回读，提交已授权范围的代码。 | 是 | 2026-11-03 |

## 3. Alternatives

- **ALT-001**: 不以Embedding阈值直接覆盖记忆；数值接近不能证明矛盾。
- **ALT-002**: 不让模型直接合并重写记忆；补充关系保留原文，替换需要明确授权。

## 4. Dependencies

- **DEP-001**: 现有MemoryService、MemoryCandidateWorker、ChatProviderService与在线Embedding配置。

## 5. Files

- **FILE-001**: backend/app/services/memory_conflict_service.py负责短快照、候选召回、模型判断。
- **FILE-002**: backend/app/services/memory_conflict_policy.py与memory_service.py负责确定性约束与版本化变更。
- **FILE-003**: backend/app/api/routes/memories.py、frontend/src/components/settings-center.tsx负责用户审核。

## 6. Testing

- **TEST-001**: 不同标题同义/否定/数值变化、同标题补充、不同条件、低置信和高影响、恶意内容与模型格式错误。
- **TEST-002**: 相关记忆/候选修改、撤销、审批并发及新事实插入后拒绝旧分析；同批候选不同时自动冲突激活。
- **TEST-003**: 在线DeepSeek-V3.2、真实Embedding/数据库/Worker/浏览器，frontend lint/build与完整后端回归。

## 7. Risks & Assumptions

- **RISK-001**: 有界语义召回可能漏候选，诊断需记录覆盖范围，不宣称可证明任意自然语言事实一致。
- **RISK-002**: 模型故障不能默认为无冲突；保留待审状态，不删除旧记忆。
- **ASSUMPTION-001**: 历史无分析记录的候选通过API启用前先补分析；原测试/设计记录保留历史口径。

## 8. Related Specifications / Further Reading

- [长期记忆正确性与版本治理](../docs/163_长期记忆第一步_正确性与版本治理_2026-10-08.md)
- [记忆召回与注入](../docs/165_长期记忆第三步_混合召回与注入诊断_2026-10-09.md)
