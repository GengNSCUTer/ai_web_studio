---
goal: 工具候选分层召回与持久化索引
version: 1
date_created: 2026-11-03
last_updated: 2026-11-03
owner: AI Web Studio
status: Completed
tags: [tools, retrieval, migration]
---

# Introduction

![Status: Completed](https://img.shields.io/badge/status-Completed-brightgreen)

将进程内全目录向量遍历改为独立PostgreSQL工具索引。保留现有执行权限体系，完成粗召回30、精排6及可观测降级；不新增可执行工具。

## 1. Requirements & Constraints

- **REQ-001**: 先按当前Catalog、Skill与用户禁止项硬过滤，索引结果只能来自本次允许集合。
- **REQ-002**: 工具说明与向量持久化；使用既有Embedding配置scope隔离用户、模型、维度、地址与凭据轮换，不存明文密钥。
- **REQ-003**: 关键词倒排与向量各召回最多30，融合后最多30；仅对该集合做详细规则与语义精排，Planner默认6个完整Schema。
- **REQ-004**: 常见维度建立HNSW表达式索引，当前默认1024维；小目录及不支持维度走明确标注的数据库精确搜索。
- **REQ-005**: 说明变更清除对应旧向量，保存Embedding时比较原说明，禁止过期结果覆盖新说明；当前允许集合与说明需参与搜索校验。
- **REQ-006**: 提供离线预建命令；冷目录每请求最多补32个，优先粗召回相关项，避免首次Chat嵌入数千工具。
- **SEC-001**: 网络等待不持有SQL事务；只嵌入脱敏工具说明和问题，不嵌入凭据、Tool返回或负向说明。
- **CON-001**: 不使用Ollama，不加新数据库服务，不改变Executor的权限、审批和风险检查。
- **PAT-001**: 沿用短Session、显式Alembic、固定账号在线验证与既有Tool Gold Set。

## 2. Implementation Steps

### Implementation Phase 1

- **GOAL-001**: 完成工具索引与有界增量更新。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | 在models/tool_search.py、迁移20261103_03新增独立工具索引、GIN和分维度HNSW；注册模型。 | 是 | 2026-11-03 |
| TASK-002 | tools/search_index.py实现增量说明更新、原说明比较写向量、带用户/scope/允许集合的稀疏和向量查询及小目录精确搜索。 | 是 | 2026-11-03 |
| TASK-003 | semantic_retrieval.py替换进程缓存为持久索引；每请求最多补31工具加1条问题，支持warmup全量预建；取消128工具语义禁用。 | 是 | 2026-11-03 |

### Implementation Phase 2

- **GOAL-002**: 完成分层筛选与验收。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-004 | selector.py先硬过滤，再粗召回30、精排6；记录各阶段数量与降级原因，保留旧同步规则兜底。 | 是 | 2026-11-03 |
| TASK-005 | backend/scripts/build_tool_search_index.py提供指定用户的预建和状态输出；索引说明不得作为执行授权。 | 是 | 2026-11-03 |
| TASK-006 | 增加隔离PG集成测试、1000工具近邻对照/查询计划/重启复用测试；复跑58例候选、在线Chat和全量回归。 | 是 | 2026-11-03 |
| TASK-007 | 更新docs/198及当前入口，飞书定点同步并回读，交付说明代码职责和真实边界。 | 是 | 2026-11-03 |

## 3. Alternatives

- **ALT-001**: 独立向量服务或本地HNSW文件会新增部署、授权隔离和文件一致性负担，本轮不采用。
- **ALT-002**: 全目录LLM选工具增加成本且仍受Schema窗口影响，保留Planner只看精排候选。

## 4. Dependencies

- **DEP-001**: 现有PostgreSQL35433、pgvector0.8.5及在线Embedding配置；不升级扩展。

## 5. Files

- **FILE-001**: backend/app/services/tools/{selector,semantic_retrieval,search_index}.py承担筛选、在线嵌入及数据库索引。
- **FILE-002**: backend/app/models/tool_search.py、backend/migrations/versions/20261103_03_工具检索索引.py及models/__init__.py承担持久化。
- **FILE-003**: backend/tests/test_tool_search_index.py和test_tool_semantic_selector.py验证准确性、隔离与降级。

## 6. Testing

- **TEST-001**: 真实PG校验用户/模型/维度/说明隔离、禁用/删除不召回、过期Embedding写入不覆盖、并发更新和进程重启后向量复用。
- **TEST-002**: 千工具固定随机向量对照ANN和精确Top30，并检查HNSW执行计划；这不是业务召回率。
- **TEST-003**: 固定58例旧/新候选对照、禁止项泄漏、在线模型/工具最终回答、全量后端含PG回归和compileall。

## 7. Risks & Assumptions

- **RISK-001**: ANN过滤后可能不足30，使用迭代扫描及明确诊断的精确补齐；不承诺近似检索永远与精确排名相同。
- **RISK-002**: 大目录冷启动不保证全量语义覆盖，诊断标出待索引数；上线前通过预建命令完成，不在用户请求中无限建库。
- **ASSUMPTION-001**: Catalog仍在请求内加载与硬过滤完整定义；本轮减少详细打分/向量传输，不宣称已消除Catalog装载的O(N)。

## 8. Related Specifications / Further Reading

- [当前候选实现与基线](../docs/172_工具候选混合召回与验收_2026-10-11.md)
- [pgvector索引与过滤](https://github.com/pgvector/pgvector#filtering)
- [PostgreSQL全文检索索引](https://www.postgresql.org/docs/current/textsearch-indexes.html)
