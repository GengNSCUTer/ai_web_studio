---
goal: Chat 历史与长期记忆并发容量优化及 Redis 实测
version: 1.0
date_created: 2026-11-05
last_updated: 2026-11-05
owner: AI Web Studio
status: In progress
tags: [performance, memory, database, cache]
---

# Introduction

![Status: In progress](https://img.shields.io/badge/status-In_progress-yellow)

合并面试复盘中的有界读取和容量验证：先测旧实现，再优化同一请求的读取量，最后用相同数据库、连接池和负载比较。PostgreSQL 保留正式事实；Redis 仅在实测收益与撤销一致性均有证据后用于派生缓存。

## 1. Requirements & Constraints

- **REQ-001**: 使用独立临时 PostgreSQL 数据库，测试 100/1,000/10,000 条历史与 50/500/1,000 条记忆，比较并发 1/8/16 的延迟、吞吐、连接等待、SQL 次数及读取行数。
- **REQ-002**: Chat 读取近期窗口及最早尚未摘要的有界批次；记忆提取按游标读取，不在每轮加载全部历史。
- **SEC-001**: 保留摘要失效、生成版本、记忆撤销、到期、用户/项目隔离和网络等待后重读验证。
- **CON-001**: 不加载 Ollama，不修改真实用户资料，不把数据库局部压测宣传成完整服务最大并发或线上 SLA。
- **GUD-001**: 中文注释；不为本优化新增摘要哈希、冻结合同或重复权限体系。

## 2. Implementation Steps

### Implementation Phase 1

- **GOAL-001**: 获得旧代码可复现基线，作为后续实测对照。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | 新增 scripts/benchmark_chat_memory_capacity.py，独立测试库、默认 QueuePool、分层并发矩阵，保存原始 JSON | 是 | 2026-11-05 |
| TASK-002 | 在业务代码变更前运行旧实现，保留版本和资源限制 | 是 | 2026-11-05 |

### Implementation Phase 2

- **GOAL-002**: 完成有界读取和安全缓存，保持历史及记忆的正确性。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-003 | 依赖 TASK-002；改造 message_repo、bootstrap、context assembly 的历史读取和摘要复核，覆盖长历史及并发历史编辑 | 是 | 2026-11-05 |
| TASK-004 | 依赖 TASK-002；memory_candidate_runtime 按游标/源 ID 查询，避免入队和 Worker 扫描完整正文 | 是 | 2026-11-05 |
| TASK-005 | 依赖 TASK-002；优化 memory_retrieval_service；实测 Redis，缓存不包含凭据，命中仍查 PG 权限/版本，故障退回数据库 | 是 | 2026-11-05 |

### Implementation Phase 3

- **GOAL-003**: 同负载复测、完整回归、真实服务验证与文档同步。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-006 | 依赖 TASK-003/004/005；并发、撤销、过期、跨用户/项目、缓存异常和旧缓存晚回填测试 | 是 | 2026-11-05 |
| TASK-007 | 依赖 TASK-006；同负载矩阵复测；全量 unittest、compileall、浏览器与在线 API 冒烟 | 是 | 2026-11-05 |
| TASK-008 | 依赖 TASK-007；更新 docs/04—09、阶段报告和飞书入口，提交推送并核对 CI | 否 | |

## 3. Alternatives

- **ALT-001**: 全部记忆移到 Redis：不采用，撤销/版本/审计仍需事务和正式存储。
- **ALT-002**: 只扩大连接池：不采用，会放大无界读取，先降低单请求成本。
- **ALT-003**: 仅保留最近若干消息：不采用，会跳过尚未摘要的积压历史。

## 4. Dependencies

- **DEP-001**: 已运行 PostgreSQL/pgvector、Redis 和现有 SQLAlchemy/redis-py。
- **DEP-002**: 复用现有用户版本、摘要边界和 generation CAS，不引入新权限协议。

## 5. Files

- **FILE-001**: backend/app/repositories/message_repo.py 及 Chat 服务：有界读取和摘要复核。
- **FILE-002**: backend/app/services/memory_candidate_runtime.py：增量提取。
- **FILE-003**: backend/app/services/memory_retrieval_service.py、memory_repo.py：召回快照与复核。
- **FILE-004**: scripts/benchmark_chat_memory_capacity.py、backend/tests：压测和安全回归。
- **FILE-005**: docs/207_Chat与长期记忆并发容量优化_2026-11-05.md：实际结果和限制。

## 6. Testing

- **TEST-001**: 三种历史/记忆规模、三档并发的修改前后比较；记录失败，不用平均值代替 P95。
- **TEST-002**: 历史编辑/删除、摘要边界缺失、并发生成拒绝、游标积压按顺序处理。
- **TEST-003**: Redis 中断、损坏、撤销后晚回填、TTL、用户/项目隔离、模型配置变化。
- **TEST-004**: 真实 PostgreSQL 回归及全量后端、在线 API 和 Playwright 冒烟。

## 7. Risks & Assumptions

- **RISK-001**: 有界摘要必须先处理最早积压，不能直接把窗口外消息标为已总结。
- **RISK-002**: 缓存命中不能绕过撤销/版本/范围复核；TTL 不提供即时一致性。
- **ASSUMPTION-001**: 单机合成测试仅定位瓶颈，不证明互联网高并发上线容量。

## 8. Related Specifications / Further Reading

- docs/178_滚动摘要历史变更失效修复_2026-10-26.md
- SQLAlchemy 官方 QueuePool 文档和 Redis 官方 cache-aside 文档；报告中记录具体测试环境。
