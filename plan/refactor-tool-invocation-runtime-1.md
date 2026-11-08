---
goal: 收敛Chat、Durable和Document的通用执行记录、恢复协议与产物管理
version: 1
date_created: 2026-11-08
last_updated: 2026-11-09
owner: AI Web Studio
status: 'In progress'
tags: [architecture, runtime, recovery, artifact]
---

# Introduction

![Status: In progress](https://img.shields.io/badge/status-In%20progress-yellow)

基于bf86ee6及当前6cbcd6a核对用户提供的源码分析。复用Agent表、ToolExecutor和原生PG检查点；统一单次调用的身份、尝试、结果提交及恢复判断，不合并Chat/Durable Worker，不删除文档业务图。分阶段测试、文档同步、提交推送；不修改简历或Day28学习状态。

## 1. Requirements & Constraints

- **REQ-001**: 提取实际由Chat与Durable调用的ToolInvocationRuntime，统一调用身份、快照核对、尝试次数和产物归属；不得只创建无人调用的接口。
- **REQ-002**: AgentStep为执行状态事实，AgentArtifact为结果事实，Approval/Revision为写入事实；业务Checkpoint保存预算与恢复位置，原生图位置不得证明外部执行成功。
- **REQ-003**: 统一恢复决策：已提交结果优先复核复用，未提交低风险只读有界重试，副作用不明默认阻断；任何幂等或对账能力必须由服务端已实现的处理器支持，远端声明不授权重放。
- **REQ-004**: 减少Chat检查点中可由Step/Artifact重建的重复结果，记录序列化体积/耗时及引用数量；保留旧版本读取，不重置原预算。
- **REQ-005**: 分离恢复输入和脱敏审计；沿用服务端CredentialResolver取当前凭据，不将密钥放入计划。原始敏感业务参数不可安全还原时明确阻断，不能执行脱敏占位符。
- **REQ-006**: 统一产物类型、归属校验、可见性、时效和引用；允许当前用户/项目按问题发现可复用的历史业务产物，不能把内部恢复日志作为新证据或重复写入。
- **REQ-007**: 正常与故障任务分别量化恢复率、重复副作用数、恢复耗时、工具复用和成本增量；同时记录分母、数据来源及未测成本，不将单测数量当业务成功率。
- **SEC-001**: 所有提交仍受当前父租约/版本/generation/用户项目范围保护；统一层不绕过现有Executor、Skill、SSRF、审批、CAS与取消。
- **CON-001**: 不新增外部工具/权限/框架，不启动Ollama，不合并队列或强制每个工具单独排队；API默认兼容。
- **CON-002**: 不删除原失败报告，不回填未知成本；阶段性GitHub提交分别核对CI。

## 2. Implementation Steps

### Implementation Phase 1

- **GOAL-001**: 建立共用执行内核，保持各协调器事务和产品行为。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | 新增backend/app/services/tool_invocation_runtime.py，提供InvocationIdentity、InvocationOutcome、恢复策略和ToolInvocationRuntime.start_attempt/create_artifact/validate_artifact；不在统一层独自commit | ✅ | 2026-11-08 |
| TASK-002 | chat_tool_runtime.py的_begin_call/_complete_call与durable_tool_runtime.py的_prepare_tool_execution/_finish_success使用共同身份、尝试、归属和提交逻辑；documents的prepare_document_claim复用尝试限制 | ✅ | 2026-11-08 |
| TASK-003 | backend/tests/test_tool_invocation_runtime.py覆盖身份、状态、大小、脱敏、重试和归属；1282全量/在线三路径/浏览器r2通过，docs/218；按阶段提交推送 | ✅ | 2026-11-08 |

### Implementation Phase 2

- **GOAL-002**: 将恢复决策集中到共享内核，业务检查点改为产物引用优先。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-004 | tool_invocation_runtime.py增加reconcile决策，Chat._begin_call和durable_graph._preflight共同处理已提交、结果缺失、未知副作用、尝试耗尽；复用前重新授权/验证版本 | ✅ | 2026-11-08 |
| TASK-005 | chat_tool_runtime.py的save/restore使用引用编码替代重复ToolCallResult/ToolTrace结果，校验Artifact哈希/范围并重建；增加体积计量，旧v1可读，新格式明确版本 | ✅ | 2026-11-08 |
| TASK-006 | 1296全量含PG/Redis、42项最终定向及在线进程退出/KILL恢复/三路径浏览器通过，检查点同内容节省17.518%，docs/219；按阶段提交推送 | ✅ | 2026-11-08 |

### Implementation Phase 3

- **GOAL-003**: 统一产物保存和可见性，提供有界的历史业务产物发现。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-007 | 新增backend/app/services/agent_artifact_store.py定义tool/evidence/draft/export/partial类型映射、归属/哈希校验、可见/时效规则；Chat/Durable/Document保存路径全部使用 | ✅ | 2026-11-08 |
| TASK-008 | tools/providers/agent_artifacts.py增加有界query/类型/时效和来源复核；可选在线预览精排需原任务明确同意；投影真实ID和小Skill候选保全，旧物理类型可读 | ✅ | 2026-11-08 |
| TASK-009 | 1313全量含PG/Redis、54定向及在线新会话list→read/文档下载/浏览器r2通过，失败r1保留，docs/220；按阶段提交推送 | ✅ | 2026-11-08 |

### Implementation Phase 4

- **GOAL-004**: 完成共用底层的成本和故障验收，不以测试绿替代业务验证。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-010 | scripts/tool_runtime_reliability_e2e.py的7场景和两个版本各10次正常对照通过；在线多轮、文档、审批及不明副作用分开记录，docs/221 | ✅ | 2026-11-09 |
| TASK-011 | 最终1314全量含PG/Redis、45定向、前端lint/build/5项及在线真实Worker/浏览器/文件通过；审阅补隐式取证生命周期传播，失败保留 | ✅ | 2026-11-09 |
| TASK-012 | 更新docs/04—07、159—162与README、AGENTS及docs/221收口；飞书定点同步回读，GitHub提交推送且CI成功，逐项审计本计划 | | |

## 3. Alternatives

- **ALT-001**: 合并Worker会把交互延迟与后台吞吐耦合，拒绝；统一单次执行而保留各业务调度。
- **ALT-002**: 全量业务正文塞LangGraph State放大泄露和重复状态，拒绝；保留业务表事实与轻图State。
- **ALT-003**: 给外部工具加replayable=True不足以判断写入结果，拒绝；未实现幂等/对账处理器一律不自动重发。

## 4. Dependencies

- **DEP-001**: 当前Supervisor单套Web/Chat/Durable/Memory/Knowledge Worker，PG35433与Redis36379；沿用原生PG连接池。
- **DEP-002**: ToolExecutor/Quality、Agent表、ChatRun、DocumentTaskService、FileRevision及CredentialResolver已有实现，不更换认证体系。

## 5. Files

- **FILE-001**: backend/app/services/tool_invocation_runtime.py为执行共用层；chat_tool_runtime.py/durable_tool_runtime.py为两类业务协调器。
- **FILE-002**: backend/app/services/agent_artifact_store.py与tools/providers/agent_artifacts.py管理产物保存/发现/读取。
- **FILE-003**: backend/app/services/document_task_service.py、document_task_worker.py及agent_graph中的三个业务图保留产品状态转换。
- **FILE-004**: backend/tests/test_tool_invocation_runtime.py及既有Chat/Durable/Document/PG专项验证；scripts/tool_runtime_reliability_e2e.py保存真实验收证据。

## 6. Testing

- **TEST-001**: 相同输入分别走Chat/Durable共享内核，质量、尝试和归属结论一致；审批草稿不算已执行成功。
- **TEST-002**: 结果前/后及图前/后崩溃，已有产物不重发；不明写入和跨call_id重复阻断；旧Worker无法提交。
- **TEST-003**: 新旧Checkpoint均可读，重复结果引用缺失/串用户/哈希不符时拒绝；参数需要脱敏不执行占位符。
- **TEST-004**: Artifact发现按用户/项目/类型/时效/来源授权过滤，内部快照不可当业务证据；保存到工作区仍审批。
- **TEST-005**: 全量后端含真实PG/Redis、前端lint/build/Node、真实模型/工具/Worker/浏览器；记录失败、分母与不保证exactly-once。

## 7. Risks & Assumptions

- **RISK-001**: 共用层误合并产品状态会扩大权限；审批、依赖传播、重排/DLQ仍由业务协调器在同事务决定。
- **RISK-002**: 旧结果引用或时效不足会污染后续任务；复用仅代表历史快照，当前授权与时效复核不可省。
- **RISK-003**: 外部只读请求未提交时可能重复计费；Token/SQL测不到的项目留null并说明，不能假造成本。
- **ASSUMPTION-001**: 当前没有已审核、提供幂等键或状态查询的外部写接口，不为展示恢复分类虚构这类能力。

## 8. Related Specifications / Further Reading

- [Chat持久化实现](../docs/217_Chat与工具统一持久化恢复_2026-11-08.md)
- [前次计划](refactor-chat-tool-recovery-1.md)
- 用户附件：883864a7-2102-4474-b139-a86deec10550/pasted-text-1.txt，作为设计分析，不将其中建议误写成已实现能力。
