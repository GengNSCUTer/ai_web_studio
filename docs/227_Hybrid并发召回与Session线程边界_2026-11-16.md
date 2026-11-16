# Hybrid并发召回与Session线程边界

2026-11-16。用户指出的串行问题存在：原Hybrid先等待向量Embedding与PG查询结束，再同步执行BM25文件读取和评分。词法读取慢时既叠加延迟，也会阻塞当前事件循环。本次只调整召回调度，不改变检索模型、RRF、过滤、阈值、精排、Top-K或知识库配置。

## 实现与线程边界

`KnowledgeRetrievalPipeline._retrieve_async`在进入两路前冻结同一generation，用两个协程并发召回，等各自结果后再融合与精排。Embedding网络等待期间BM25可以进行；同步SQL仍在拥有Session的原线程执行，不声称数据库查询本身异步并行。

BM25的文件读取、解析、分词和评分通过`asyncio.to_thread`执行。线程函数只拿普通索引实例与字符串、数字；命中ID回到原线程后才查询Chunk、做归属/generation过滤和正文组装。缺失、损坏或过时的索引先由原线程查询已授权Chunk，复制为`LexicalChunkSnapshot`标量数据，再在线程中重建与原子替换索引；不将ORM对象或Session交给线程。

现有索引搜索只使用函数内局部数据，没有共享可变搜索缓存；发布使用独立临时文件和`os.replace`，读取能见到完整的新或旧文件。结构索引的新旧时间检查改为与当前store同一index_root，避免自定义目录查错主索引。本次不承诺多进程全局锁或对正在重建的旧generation实施强行取消。

两路异常分别转换为稳定错误码，保留单路降级；双路均失败仍报`hybrid_both_unavailable`，没有资料和服务故障仍分开。`CancelledError`不当作降级，父任务或一路取消时取消并回收兄弟协程，不再查库或回填迟到结果。已开始的线程文件操作无法强行终止，但线程不持有Session；仍可能完成本次同generation的派生索引文件写入，不修改业务Chunk或活动版本。

| 文件 | 具体职责 |
| --- | --- |
| `backend/app/services/knowledge_retrieval_pipeline.py` | Hybrid并发、固定generation、取消清理；词法I/O线程隔离，DB读取留原线程 |
| `backend/app/services/knowledge_index_service.py` | 不可变`LexicalChunkSnapshot`，复用已有BM25构建和原子发布 |
| `backend/tests/test_knowledge_retrieval_pipeline.py` | 两路交叠、Session线程、快照重建、版本切换、父/子取消及原降级/精排回归 |
| `backend/tests/test_pgvector_integration.py` | 真实PG与BM25冷建/热读/损坏重建，结构模式，逐SQL线程检查，与串行参考比较全部结果元数据 |
| `scripts/hybrid_retrieval_concurrency_acceptance.py` | 串行参考和并发的耗时/排序比较，可选真实在线Embedding；失败也保留已完成受控结果 |
| `scripts/hybrid_retrieval_chat_acceptance.py` | 浏览器选择现有中文库，真实模型/SSE/来源/PG落库与命令事实验证，清理临时会话 |

## 耗时与结果验证

串行参考和并发复用相同召回、融合函数。受控测试有1,000个Chunk、Top-20，Embedding用80ms异步等待模拟。正常BM25使用真实本地文件；另一组额外注入60ms词法延迟。每组交错执行8对，均保持排序一致。

| 条件 | 串行均值 | 并发均值 | 排序相同 |
| --- | ---: | ---: | ---: |
| 80ms受控Embedding＋真实本地BM25 | 94.29ms | 81.56ms | 8/8对 |
| 上述条件＋60ms受控词法延迟 | 162.69ms | 81.80ms | 8/8对 |

这证明延迟能够交叠，不是在线API提速比例或生产P95。线程不能绕过GIL加速任意纯Python计算；真实收益取决于BM25大小、磁盘、SQL和远端等待。

对现有论文库和中文顺序修复库，额外使用库内已保存向量作为查询向量，运行真实PG与BM25串行/并发各4对。两库共8/8排序一致，全部SQL在原线程。论文库均值404.42/367.60ms，中文库152.94/106.98ms；小样本受冷缓存与并行回归负载影响，且未调用Embedding，不用它们宣称真实线上提速或语义质量。

真实在线Embedding对照请求返回HTTP402（余额不足），未完成健康双路API对照；不换Key、不改账号Provider、不加载Ollama。保留失败，不能把已有向量验证说成在线Embedding成功。

## 浏览器与业务验证

首轮ACID问题已完成Chat和引用，但检索证据只提到术语，没有充分定义；模型如实拒绝完整作答，四特性断言未通过。此为证据/任务不足记录，不降低原断言或改历史评测标签来称成功。

随后明确选择库内已有原文的Redis List阻塞队列场景：真实浏览器选库、发送问题→独立ChatWorker并发Hybrid→向量路不可用/BM25降级→在线`deepseek-flash`回答→SSE展示及PG保存。返回20候选、6来源、413字完整回答，包含BLPOP、BRPOP及KB引用；assistant=done、浏览器console/pageerror=0。仅临时项目/会话被清理，正式资料、模型与设置不变。

80项定向测试（含真实PG）通过；全量后端1,384项通过，耗时738.065秒，已配置真实PostgreSQL/pgvector集成环境，无跳过项。`compileall`与`git diff --check`通过。汇总证据见`backend/evals/results/hybrid-concurrency-acceptance-20261116.json`，受控实验、已有向量回放、在线Embedding失败和浏览器降级成功分别记录。本次没有重跑百题RAG或200题Agent业务评测，也不修改简历质量指标。

飞书当前项目入口已同步并回读至revision 165，技术架构已同步并回读至revision 61，均记录本次实现、1,384项测试及Embedding 402限制；未新增独立云文档或覆盖历史RAG质量指标。
