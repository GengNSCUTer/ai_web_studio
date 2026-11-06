---
goal: 将知识工作台的 Agent 与后台业务编排逐步迁移至 LangGraph
version: 1.0
date_created: 2026-11-06
last_updated: 2026-11-06
owner: AI Web Studio
status: 'In progress'
tags: [langgraph, migration, agent, durability]
---

# Introduction

![Status: In progress](https://img.shields.io/badge/status-In%20progress-yellow)

以已推送的 25f355b 为起点，将流程调度逐步交给 LangGraph。2026-11-06 用户已授权计划和渐进实施、GitHub 同步；每阶段完成代码、回归、真实业务、文档与推送后进入下一阶段，不把本轮第一阶段宣称为全量迁移。数据库容量优化已在 docs/208 完成，不作为本次迁移收益重复统计。

## 1. Requirements & Constraints

- **REQ-001**: 最终由 LangGraph 控制同步 Agent Loop、Chat 组装/生成编排、Durable 与文档任务、记忆提取和知识索引的业务阶段转换。
- **REQ-002**: 保留 FastAPI、Next.js、Provider、SQLAlchemy、PG/Redis、RAG、Memory、MCP、Skill、Executor 作为业务服务；HTTP 协议和事务方法不为形式迁移而改写。
- **REQ-003**: 节点拆为可观察阶段，不能将旧完整循环包成一个节点冒充迁移；避免同时存在两个决定续轮/终止的调度器。
- **REQ-004**: 普通 Chat 继续按当前模式预算执行，保持 NDJSON/generation/CAS；不因使用 Checkpointer 而承诺断点续 token。
- **REQ-005**: AgentRun/Step/Artifact/Approval 是业务状态依据；图检查点表达执行位置，落后时通过已有产物幂等补齐，不重复外部副作用。
- **SEC-001**: 原用户意图、身份与项目范围只来自认证后的服务端；工具正文和模型不能改写 Runtime 权限。
- **SEC-002**: 不把 Session、API Key、Cookie、Provider Client、Docker socket 放进图状态或外部 Trace；不默认启用 LangSmith 外传。
- **SEC-003**: 审批仍使用真实 Diff、AgentApproval、FileRevision CAS；恢复/重放重新授权。沙箱仍禁用，无新增高风险工具。
- **CON-001**: Python 3.10；LangGraph 1.2.20、langchain-core 1.7.6（均已核对截至本日发布）；依赖先 dry-run、再安装与 pip check，现有 SDK 不更换。
- **CON-002**: 现有 PG 35433、API 32007、前端 32008 和单套 Supervisor 不变；真实模型走在线 DeepSeek-V3.2，不加载 Ollama。
- **CON-003**: 迁移期间允许服务端显式选择 legacy/langgraph；运行中异常不能自动退回旧链重复调用工具。最终阶段移除临时旧入口。
- **CON-004**: 使用原有测试和固定账号；隔离库做故障/并发测试；历史评测及失败不覆盖。未跑的恢复/容量场景明确待验收。

## 2. Implementation Steps

### Implementation Phase 0 — 固定起点与依赖

- **GOAL-001**: 先推送既有功能，建立真实可回退的提交边界。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | 提交记忆索引代码、六份原始报告和 docs/208，推送 main 并核对 GitHub Actions；起点 25f355b | ✅ | 2026-11-06 |
| TASK-002 | 核对官方 StateGraph/Runtime/条件边/持久化语义和 PyPI 发布日期；锁定 CON-001 依赖并运行 pip check | ✅ | 2026-11-06 |
| TASK-003 | 梳理代码责任：ExternalContextService/ToolWorkflow/ChatContextAssembly/ChatStream/DurableToolWorker/DocumentTaskWorker/MemoryCandidateWorker/KnowledgeWorker；写 docs/209 和每阶段退出标准 | ✅ | 2026-11-06 |

### Implementation Phase 1 — 同步工具循环

- **GOAL-002**: LangGraph 实际决定规划、执行、观察、续轮和终止，保持现有行为。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-004 | 将 external_context_service.py 的单轮业务逻辑提取为 tools/loop_runtime.py：plan_round、execute_round、observe_round、finish；旧入口只调用同一组步骤用于对照 | ✅ | 2026-11-06 |
| TASK-005 | agent_graph/sync_tool_graph.py 建立 plan→execute→observe→plan/finalize 条件边；每请求独立 Runtime，State 记录轮数/动作/结束原因/计数，不包含凭据 | ✅ | 2026-11-06 |
| TASK-006 | ExternalContextService 接入编排选择；模式时长/轮次/调用/重规划额度、ActionIntent、证据投影和最后统一来源保持不变；图递归上限独立设置且高于业务合法路径 | ✅ | 2026-11-06 |
| TASK-007 | 对照两种引擎测试无工具、两轮修复、依赖失败、部分结果、参数拒绝、总超时、取消、重复call_id、Skill完成；运行全量、真实在线工具→最终回答和浏览器 | ✅ | 2026-11-06 |
| TASK-008 | 正式默认路径切至 langgraph，保留显式旧引擎用于短期对照；记录 graph 节点进展与现有 Trace 映射，更新飞书并提交 | ✅ | 2026-11-06 |

退出标准：节点确实分别调用 Planner/Workflow/观察逻辑；既有业务回归通过；取消不会执行备用引擎；当前阶段不创建跨请求图检查点。

### Implementation Phase 2 — Chat 主链与状态

- **GOAL-003**: 请求准备、检索/记忆、工具图、预算和生成阶段具有统一图入口。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-009 | 新建 agent_graph/chat_graph.py 和 chat_graph_state.py；只保存消息ID/generation、证据引用、阶段诊断，认证对象与Provider配置走 Runtime Context | | |
| TASK-010 | chat_execution_service.py 与 chat_context_assembly_service.py 接入 prepare→retrieve_memory/rag→tools_subgraph→govern→generate/finalize；只并行无依赖只读阶段，保留各自失败降级 | | |
| TASK-011 | Chat流式服务将图事件适配现有NDJSON；重复/旧generation写入仍拒绝；首包/空闲/总超时、用户停止、代理断连各保持现有收口 | | |
| TASK-012 | 真实模型验证普通/RAG/Tool/混合四模式、停止、超时、重生成/编辑重答；前端无接口破坏，实际注入来源一致 | | |

退出标准：核心 Chat 已走图入口；只读并行无跨线程共享 Session；滚动摘要/记忆调用前仍释放连接，失败不伪装成成功。

### Implementation Phase 3 — Durable 检查点与 Worker

- **GOAL-004**: 图恢复和现有业务幂等、租约在同一设计下工作。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-013 | 锁定 langgraph-checkpoint-postgres 3.2.0，显式迁移建立项目专用 checkpoint schema；连接池/lifespan 独立管理；不通过Web请求自动setup生产表 | | |
| TASK-014 | 新建 agent_graph/durable_graph.py、durable_checkpoint_service.py，将 user/project/run/attempt 与 thread_id/checkpoint_ns 明确映射；历史旧Run继续按原runtime版本恢复 | | |
| TASK-015 | durable_tool_runtime.py Worker 仍按现有Lease领取/续租；checkpoint写入和步骤完成均校验owner/version。图节点先查已保存Artifact，完成则复用；外部调用不能因检查点落后重复执行 | | |
| TASK-016 | 测“外部执行前/产物提交后/图检查点前/消息回传前”四处强制退出、双Worker抢占、旧worker迟到、重复handoff；检查结果只回传一次 | | |

退出标准：进程重启可从已完成业务阶段继续；checkpoint不是第二份独立事实库；租约过期旧Worker不得写图或业务进度。

### Implementation Phase 4 — 审批暂停与恢复

- **GOAL-005**: interrupt/resume 与现有真实审批合并成一条可验证链路。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-017 | 新建 agent_graph/approval_nodes.py：在写入前interrupt，先以唯一业务键保存PatchDraft/Approval；暂停释放Worker资源，安装/激活不等于执行批准 | | |
| TASK-018 | agent_runtime.py 审批恢复只接受当前用户授权的approval_id/版本；重新检查Diff参数、基线版本、风险、工具版本，拒绝后收口，禁止模型伪造恢复 | | |
| TASK-019 | 浏览器真实确认/拒绝/重复确认，另一会话修改、过期批准、跨用户恢复、暂停后重启、取消；复核真实文件和FileRevision只有预期一次变化 | | |

退出标准：审批状态、图暂停状态和任务状态一致；resume前重进节点不能重复建审批/重复写文件。

### Implementation Phase 5 — 文档交付与可恢复子图

- **GOAL-006**: 文档生成和交付由图节点组织，保留下载与可选保存的产品语义。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-020 | document_task_service.py/document_task_worker.py 接 document_graph.py，节点为取证→草稿→导出→完成；保存到工作区另接审批子图 | | |
| TASK-021 | 已完成阶段通过Artifact引用恢复；草稿中断重新生成，不续token；联网/模型/导出各自错误分类，成功产物不丢失 | | |
| TASK-022 | 在线模型+真实文件+浏览器验证Markdown/Word下载、重复请求、停止/部分失败、导出后重启、可选批准保存与原会话结果卡片 | | |

退出标准：导出成功即可下载，不强制等待写入批准；阶段失败可解释、恢复不重跑已完成副作用。

### Implementation Phase 6 — 记忆与索引后台阶段

- **GOAL-007**: 将剩余后台业务阶段组织为可测试子图，不改变队列与存储事实。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-023 | memory_candidate_runtime.py 接 memory_extraction_graph.py：固定源快照→模型提取→原文/冲突核验→短事务保存→游标推进；向量补建仍用现有索引服务 | | |
| TASK-024 | KnowledgeWorker 的解析→切分→embedding→BM25/向量准备→active generation切换接 knowledge_index_graph.py；Outbox/Redis消费/提交后ACK/Lease在图外基础设施层保留 | | |
| TASK-025 | 真实隔离库测试记忆源编辑/撤销、重复Job、模型失败、索引半成品/重启/重复消息、旧generation继续可读；不将图state当Redis任务或DB事务 | | |

退出标准：重复投递仍幂等；步骤检查点不提前ACK；记忆范围/撤销和当前数据库候选性能不回退。

### Implementation Phase 7 — 收口、删除临时旧实现

- **GOAL-008**: 清理双引擎过渡代码，形成真实的LangGraph项目口径。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-026 | 完成新引擎固定业务集+独立留出对照：Candidate/Planner/执行/最终任务分别统计；覆盖现有200任务的适用范围并保留原始错误，不拼接旧分数 | | |
| TASK-027 | 删除legacy同步循环、临时选择配置和无调用适配；兼容历史Run仅保留有实际存量的版本路径，退役条件写入docs/209 | | |
| TASK-028 | 全量后端/PG/Redis、前端lint/build/浏览器、在线模型、强制重启/重复确认/取消/安全边界、并发资源回收验证；更新部署和依赖迁移说明 | | |
| TASK-029 | 归档旧架构说明，更新docs/04—09/当前全景/飞书/简历与面试材料；只声称真实已完成的图恢复、业务效果和边界，提交并确认CI | | |

退出标准：图为唯一当前编排入口，现有权限和业务测试保留，历史任务可解释，最后有功能/故障/成本对照及明确未验证项。

## 3. Alternatives

- **ALT-001**: 不一次性替换全部Runtime：会同时改变审批、副作用和消息一致性，故采用逐阶段明确提交。
- **ALT-002**: 不直接换成预置通用Agent/ToolNode后放弃Executor：现有业务边界仍需代码校验。
- **ALT-003**: 不把SQL查询、HTTP代理、Redis协议都做成图节点；迁移对象是业务阶段编排。
- **ALT-004**: 不把Graph Checkpoint当正式消息/长期记忆表；保留业务资源和授权入口。

## 4. Dependencies

- **DEP-001**: 各阶段按0→1→2→3→4→5→6→7执行；阶段内测试可独立，发布和数据库变更串行。
- **DEP-002**: 现有PG/Redis/Provider/Worker和固定账号；实际Python容器仍无Rootless部署前提，不计入迁移成功。

## 5. Files

- **FILE-001**: backend/app/services/agent_graph/：同步、Chat、Durable、审批、文档、记忆和索引图。
- **FILE-002**: backend/app/services/tools/loop_runtime.py、external_context_service.py：提取单轮逻辑与门面。
- **FILE-003**: backend/app/services/chat_execution_service.py、chat_context_assembly_service.py 及现有流式服务：事件/状态适配。
- **FILE-004**: backend/app/services/durable_tool_runtime.py、document_task_service.py、document_task_worker.py、memory_candidate_runtime.py、知识索引Worker：分阶段接入。
- **FILE-005**: backend/requirements.txt、迁移、配置样例、部署说明、专项测试及真实验收脚本。
- **FILE-006**: docs/209_LangGraph全量迁移计划与阶段记录_2026-11-06.md、docs/04—09、README、AGENTS 与飞书当前入口。

## 6. Testing

- **TEST-001**: 每阶段运行相关unittest和compileall；发布前完整discover，PG/Redis集成使用独立库且清理。
- **TEST-002**: 同步引擎对照归一化忽略实际耗时，比较计划、工具调用、依赖、结果/来源、停止原因和副作用次数。
- **TEST-003**: 在线模型→真实Tool→最终回答→落库→浏览器的正常与失败路径；只调工具HTTP不算端到端。
- **TEST-004**: cancel/timeout/错误后不透明重试旧引擎；不丢弃已成功来源；跨请求/跨用户图状态不串用。
- **TEST-005**: Durable强制进程退出、并发旧worker、检查点落后、审批重入、DLQ受控恢复，验证真实业务状态与产物。
- **TEST-006**: 依赖与数据库版本、健康接口、单套托管服务、敏感信息和外部Trace检查；只有实际CI成功才报告CI成功。

## 7. Risks & Assumptions

- **RISK-001**: 图重入可能重复外部动作，必须用现有业务幂等和阶段产物复核；图持久化不等于exactly-once。
- **RISK-002**: 图的superstep/递归次数不等于Planner轮次；配置独立递归保护，原业务预算继续有效。
- **RISK-003**: Graph与业务DB提交不共享事务，恢复协议必须容许checkpoint滞后，禁止仅凭checkpoint宣布写入成功。
- **RISK-004**: 迁移不保证模型更准确、响应更快；收集开销与维护差异，发现回退先修复而不删除测试。
- **ASSUMPTION-001**: 本轮优先完成阶段0/1；后续按同一计划逐项推进，不以文档勾选替代验证。

## 8. Related Specifications / Further Reading

- [现有架构与记忆改造](../docs/208_长期记忆数据库召回与LangGraph迁移取舍_2026-11-05.md)
- [LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)
- [Durable execution](https://docs.langchain.com/oss/python/langgraph/durable-execution)
- [Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)
- [Interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
