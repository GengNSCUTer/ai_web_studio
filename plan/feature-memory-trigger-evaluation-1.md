---
goal: 长期记忆触发决策记录、上下文特征与低频语义判断
version: 1.0
date_created: 2026-11-11
last_updated: 2026-11-11
owner: Codex
status: 'Completed'
tags: [feature, memory, evaluation]
---

# Introduction

![Status: Completed](https://img.shields.io/badge/status-Completed-brightgreen)

在已有确定性触发规则上增加可审计记录、离线标注评测、有限上下文特征及仅针对 `buffer` 的低频语义判断。不会把每轮 Chat 变成额外模型调用，也不改变原文核验、冲突处理和启用权限。

## 1. Requirements & Constraints

- **REQ-001**: 记录每次触发判断、原因、上下文特征、是否创建 Job 和最终语义判断；不得保存原始消息正文、密钥或敏感字段。
- **REQ-002**: 提供可版本化的标注集和离线指标：Trigger Precision、Trigger Recall、误触发率、漏触发率、语义判断调用率及额外模型调用次数。
- **REQ-003**: 审计特征只使用当前会话/授权范围的计数和布尔信号；语义输入可使用本会话最多四条脱敏上下文和范围过滤的已有记忆摘要，不能读取其他项目私有正文。
- **REQ-004**: 只有 `buffer` Job、默认关闭的复核与自动候选均开启、至少4来源、每用户60秒一次、每Job一次预留时调用当前模型，15秒/240输出Token。高置信skip消费、buffer保留，满50条buffer回原提取释放扫描空间；失败/低置信回原链，已提交判断恢复不重复调用。
- **SEC-001**: 语义判断不能激活、删除、替换或修改记忆；外部引文仍不能成为控制请求。
- **CON-001**: 使用显式 Alembic 迁移和真实 PostgreSQL 回归；不引入新数据库，不自动改变用户已有设置。

## 2. Implementation Steps

### Implementation Phase 1

- **GOAL-001**: 建立触发决策记录和离线评测。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | 新增 MemoryTriggerDecision、迁移、enqueue 记录与拥有者标注API；来源版本和观测回合区分 | ✅ | 2026-11-11 |
| TASK-002 | 48例版本化标注集、规则回放/真实标签导出、二分类与五分类独立统计；保留三条漏例 | ✅ | 2026-11-11 |

### Implementation Phase 2

- **GOAL-002**: 引入上下文特征和低频语义门控。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-003 | 批量读取有界历史与范围计数，审计不存正文；语义复核结合有限本会话和已有记忆上下文 | ✅ | 2026-11-11 |
| TASK-004 | LangGraph buffer-only 分支、设置UI、用户间隔预留与恢复，保持原证据/冲突/启用链 | ✅ | 2026-11-11 |

### Implementation Phase 3

- **GOAL-003**: 完成回归、真实 Worker 和资料同步。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-005 | 1356项全量、最终96项定向含10项PG；覆盖审计隐私、权限、频率、恢复、满窗、源变更、低置信/超时、上下文隔离 | ✅ | 2026-11-11 |
| TASK-006 | DeepSeek官方新两会话五轮及原六会话九轮通过，docs/223与当前四份专题同步回读（入口153/产品14/技术53/上下文25） | ✅ | 2026-11-11 |

## 3. Alternatives

- **ALT-001**: 每轮 Chat 调用一次重要性模型：成本和延迟固定增加，拒绝。
- **ALT-002**: 继续增加正则关键词而不记录样本：无法测量误触发/漏触发，拒绝。

## 4. Dependencies

- **DEP-001**: 现有 MemoryExtractionJob、LangGraph 提取图、Provider Service 和 PostgreSQL 迁移链。
- **DEP-002**: 现有 source_digest、范围校验、用户锁、租约和 activity 通知。

## 5. Files

- **FILE-001**: backend/app/models/memory_trigger_decision.py、models/__init__.py、migration：决策记录。
- **FILE-002**: backend/app/services/memory_trigger_policy.py、memory_candidate_runtime.py：上下文特征和入队绑定。
- **FILE-003**: backend/app/services/memory_semantic_gate.py、agent_graph/memory_extraction_graph.py：低频判断。
- **FILE-004**: backend/evals/memory_trigger_labeled_cases.json、scripts/evaluate_memory_trigger_policy.py：标注与指标。
- **FILE-005**: backend/tests/test_memory_trigger_evaluation.py、现有触发/PG测试：回归。

## 6. Testing

- **TEST-001**: 每个决策不含正文/凭证或其他项目私有计数；同一观测回合/消息/digest/策略不重复，编辑后新增版本。
- **TEST-002**: 标注集指标可复现；规则版本和样本版本写入报告。
- **TEST-003**: 语义调用只属于获准buffer Job；开关/用户冷却/最小批次/Job预留限制可减少调用。普通问题零次；高置信skip不提取，extract_now复用原证据核验。
- **TEST-004**: Provider 失败、非法 JSON、跨项目记忆、并发入队和租约失效不扩大权限或推进错误游标。

## 7. Risks & Assumptions

- **RISK-001**: 构造标注集不能代表真实用户语言；报告必须分开写构造指标和线上样本。
- **RISK-002**: 语义判断模型也会误判；它只能控制是否进入提取，不得直接写记忆。
- **ASSUMPTION-001**: 低频语义判断使用用户当前 Provider，失败时保守沿用原提取路径，不静默失败为已保存。

## 8. Related Specifications / Further Reading

- plan/feature-memory-event-trigger-1.md
- docs/222_长期记忆事件触发与持久缓冲_2026-11-11.md
