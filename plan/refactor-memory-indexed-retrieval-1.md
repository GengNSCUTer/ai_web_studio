---
goal: PostgreSQL 记忆有界混合检索与缓存版本检查
version: 1
date_created: 2026-11-05
last_updated: 2026-11-05
status: 'Completed'
tags: [memory, performance, retrieval]
---

# Introduction

![Status: Completed](https://img.shields.io/badge/status-Completed-brightgreen)

将生产 PostgreSQL 路径从全部正文/JSON 向量搬入 Python，改为数据库词法/向量召回后仅读取有限候选。保留 SQLite 业务测试兼容路径，不迁移 LangGraph。

## 1. Requirements & Constraints

- **REQ-001**: 生产冷查询只向应用返回有界候选；相关的旧记忆不能仅因不够新而被排除。
- **REQ-002**: Redis 命中使用用户记忆修订号和到期边界，不读取全部版本清单。
- **SEC-001**: 读取和最终注入同时校验用户、项目、来源、状态、TTL、敏感信息、正文版本和模型签名。
- **CON-001**: 不加载 Ollama；不替换数据库/端口；不开放新工具或修改 LangGraph 编排。
- **CON-002**: 向量缺失时支持有限批次补建；后台补建与模型网络调用分离短数据库事务。

## 2. Implementation Steps

### Implementation Phase 1

- **GOAL-001**: 固定旧基准并实现数据库索引与读取。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | 复用 docs/207 和旧脚本，另存不同问题旧基准；审阅现有权限和排序测试 | ✅ | 2026-11-05 |
| TASK-002 | user_memories 新增派生词法/原生向量索引；users 新增记忆修订号；迁移和建表采用同一触发器定义 | ✅ | 2026-11-05 |
| TASK-003 | memory_repo.py 提供有界候选读取；memory_retrieval_service.py 保留最终复核与原排序 | ✅ | 2026-11-05 |

### Implementation Phase 2

- **GOAL-002**: 补建、性能和业务验收。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-004 | 复用后台记忆 Worker 补建缺失向量，失败退避；未完成补建在诊断中明确显示 | ✅ | 2026-11-05 |
| TASK-005 | PostgreSQL/Redis 测试旧事实、无词法重合、跨用户/项目、撤销、TTL、设置变化、坏向量和并发 | ✅ | 2026-11-05 |
| TASK-006 | 比较新问题 1/8/16 并发 P95、读取数、SQL 和召回；在线模型/浏览器回归；更新文档和飞书 | ✅ | 2026-11-05 |

## 3. Alternatives

- **ALT-001**: 不把全部记忆放入 Redis，避免持久状态迁移和内存容量问题。
- **ALT-002**: 不用最新 N 条代替相关性检索，避免永久漏掉旧事实。
- **ALT-003**: 不全量迁移 LangGraph；编排框架不能替代数据库检索设计。

## 4. Dependencies

- **DEP-001**: 现有 PostgreSQL/pgvector、Redis、SQLAlchemy 和在线 Embedding。

## 5. Files

- **FILE-001**: backend/app/models/user.py、user_memory.py 与数据库迁移：修订号、派生索引。
- **FILE-002**: backend/app/repositories/memory_repo.py、backend/app/services/memory_retrieval_service.py：读取与复核。
- **FILE-003**: 后台记忆 Worker、专项测试、基准脚本和 docs/208 实施报告。

## 6. Testing

- **TEST-001**: 未命中缓存时不能回到应用全量正文/向量排序；测试返回行数上限。
- **TEST-002**: 记忆变更和来源范围变化即时失效；过期/撤销不会由旧缓存复活。
- **TEST-003**: 无词法重合的旧事实通过数据库向量召回；索引缺失、错误和超时有明确降级。
- **TEST-004**: 多并发不同问题与旧基准对照，另列组件测试和完整 HTTP/模型测试的边界。

## 7. Risks & Assumptions

- **RISK-001**: 有界候选可能改变排序；必须对照正确记忆召回，不能只报速度。
- **RISK-002**: ANN 严格范围过滤可能不足；小范围精确，大范围HNSW并按数量/分数补精确；受控测试不代替真实记忆质量大样本验证。
- **ASSUMPTION-001**: 本轮首先消除全量向量传输/解析；不宣称数据库无限并发或已经通过长期线上压测。

## 8. Related Specifications / Further Reading

- [已完成容量基准](../docs/207_Chat与长期记忆并发容量优化_2026-11-05.md)
- [pgvector 官方实现](https://github.com/pgvector/pgvector)
