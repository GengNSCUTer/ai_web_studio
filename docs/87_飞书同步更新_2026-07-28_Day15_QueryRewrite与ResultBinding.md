# 2026-07-28 Day 15：RAG Query Rewrite 与 Tool Result Binding

本轮从简历主张反查代码边界，只实现了两项能够在现有架构内小步验证的能力，没有把复杂状态机用进程内状态伪装完成。

## 已完成

RAG 检索新增确定性的短指代 Query Rewrite。当当前问题包含“它、这个、上述、前者/后者”等明确指代时，系统只使用最近一条历史 user 消息扩展实际检索词；Assistant 内容不参与改写。原始问题继续用于最终回答和 Retrieval Log 主字段，diagnostics 记录原问题、实际检索词、策略和上下文消息 ID。它不是 LLM Rewrite、Multi-step RAG 或 PCR-RAG。

Bounded Tool Workflow 新增受控 Result Binding。LLM Planner 只声明绑定关系，后端代码等待上游成功后，从已声明依赖上游的 `ExternalSource.metadata.raw` 按受限 JSON Pointer 取值，写入下游顶层参数，再重新执行完整 Input Schema 校验，最后由 Executor 发起真实 Tool/MCP 请求。

安全边界包括：禁止读取自由文本 `display_text`；禁止未声明字段、重复目标、模型参数覆盖目标和无依赖来源；只允许有长度上限的标量或短标量数组；缺失、越界、类型错误或 Schema 不通过时，下游调用不执行；Trace 不记录实际绑定值。

## 已有能力核验

Knowledge Job 已经具备稳定 event ID、Job 状态幂等、Lease Version、Heartbeat、CAS/Fencing、有限重试和数据库 dead-letter 状态，因此本轮没有重复实现第二套。

## 仍保留的架构边界

高风险 Tool 的用户确认后 continuation、外部副作用跨请求持久化幂等、Agent Run/Step Checkpoint、崩溃后从某个 Tool Step 恢复，以及 Knowledge Job 周期对账、DLQ 受控重放和跨系统补偿仍未完成。这些能力需要持久数据库状态机、参数哈希、审批协议、lease/version 和外部副作用核对，不能靠内存 Map 正确解决。

该阶段记录的是较早版本的 Bounded Tool Workflow 与受控结构化 Result Binding；不是任意 DAG 编排平台或 Durable Agent Runtime。后续已将同步 Chat 上限统一为五轮，当前口径以 `ExternalContextService.max_agent_rounds = 5` 为准。

## 验证结果

- 后端 `compileall` 通过。
- Query Rewrite、Planner、Workflow、Executor、Router 和 Tool Integration 定向 107 项测试通过。
- 后端全量运行 212 项，结果为 `OK (skipped=3)`；3 项 PostgreSQL/pgvector 集成测试因未配置专用测试数据库条件跳过。
