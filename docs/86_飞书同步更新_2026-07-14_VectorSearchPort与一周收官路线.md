## Vector Search Port 与一周收官路线

### 本轮代码进展

新增 `KnowledgeVectorSearch` Protocol，`KnowledgeRetrievalPipeline` 只依赖“返回 top-k Chunk 和 score”的搜索契约，不再根据数据库 dialect 选择 pgvector 或 FAISS。

生产默认使用 `PgvectorKnowledgeVectorSearch`。旧 SQLite 测试只有在显式注入 `faiss_store` 时才使用临时 `LegacyFaissVectorSearchAdapter`。下一步将把旧测试改为显式 Fake/InMemory Vector Search，然后删除 FAISS 类和依赖。

验证：

```text
Knowledge tests: 45 OK
PostgreSQL/pgvector integration: 3 OK
Full backend including integration: 105 OK
Backend health: HTTP 200
```

### 一周收官路线

| 日程 | 主线 | 产出 |
| --- | --- | --- |
| Day 1 | pgvector 收尾、Vector Search Port、删 FAISS 耦合 | 无 FAISS 主路、索引写/读流程 |
| Day 2 | generation、inactive 快照、CAS、并发重索引 | 故障注入测试、并发状态推演 |
| Day 3 | BM25、RRF、Hybrid、Rerank、PostgreSQL 全文检索评估 | 算法小样例和技术取舍记录 |
| Day 4 | Chat Route -> Context -> Provider -> Streaming -> Message | 流式状态机与失败恢复实验 |
| Day 5 | Tool Catalog -> Planner -> Executor -> MCP -> Trace -> Memory | 多步 Agent 轨迹和副作用安全清单 |
| Day 6 | Auth、Secret、Conversation、Upload/Parser、Job、可观测性 | 安全清单、事务边界图、生产差距清单 |
| Day 7 | 综合回归、运行手册、README、架构图、简历与模拟面试 | 一键启动/测试、项目描述、STAR 故事和面试问答 |

一周内优先完成主链正确性、数据一致性、安全、可测试性和求职表达；暂停新前端功能、新 Agent 花活功能和无指标的大重构。
