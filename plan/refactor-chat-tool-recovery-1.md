---
goal: 普通Chat复用Agent运行记录、工具产物与受租约保护的图检查点
version: 1
date_created: 2026-11-08
last_updated: 2026-11-08
owner: AI Web Studio
status: 'In progress'
tags: [architecture, chat, recovery, langgraph]
---

# Introduction

![Status: In progress](https://img.shields.io/badge/status-In%20progress-yellow)

保留ChatRun作为会话和事件事实，关联runtime_kind=chat_tool_workflow的AgentRun。
普通工具循环记录规划阶段、AgentStep和AgentArtifact；恢复时从业务提交位置重建图运行时。

## 1. Requirements & Constraints

- **REQ-001**: 已提交工具结果可复用；并发分支分别提交，不等待整批成功才持久化。
- **REQ-002**: 保存规划轮次、计划、观察、预算和下一节点；原生PG检查点只存ID和计数。
- **REQ-003**: 新ChatWorker接管必须重新验证用户、会话、generation、租约及当前工具/Skill权限。
- **REQ-004**: 最多三次Worker领取；最终生成最多两次；保持首次领取的总截止时间。
- **SEC-001**: 未提交结果的副作用工具不自动重放；文件审批和Revision CAS保持原路径。
- **SEC-002**: 不持久化客户端、Session、认证凭据；脱敏改变参数时禁止恢复执行该调用。
- **CON-001**: 不加载Ollama，不扩大工具权限，不新增服务角色，不改RAG评测口径。

## 2. Implementation Steps

### Implementation Phase 1

- **GOAL-001**: 工具循环和执行结果持久化。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | 新增chat_tool_runtime.py、tools/runtime_codec.py；使用既有AgentRun/Step/Checkpoint/Artifact表记录循环和调用 | ✅ | 2026-11-08 |
| TASK-002 | sync_tool_graph.py按业务恢复位置进入节点，接入受Chat租约保护的原生PG Saver；Executor接入逐调用结果复用 | ✅ | 2026-11-08 |

### Implementation Phase 2

- **GOAL-002**: Chat接管和生成重启闭环。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-003 | chat_run_worker/service接入持久化上下文；有恢复记录的Run有限接管，保留原总截止时间 | ✅ | 2026-11-08 |
| TASK-004 | 重新生成前归档旧片段、发送run_snapshot重置；chat-thread支持NDJSON和SSE重置 | ✅ | 2026-11-08 |
| TASK-005 | 补SQLite及真实PG进程退出、旧租约、取消、权限变化、写入不明和在线模型浏览器测试 | ✅ | 2026-11-08 |
| TASK-006 | 全量回归、文档、飞书回读、GitHub和CI收尾 | | |

## 3. Alternatives

- **ALT-001**: 仅写AgentStep审计不支持恢复规划，不能满足REQ-002。
- **ALT-002**: 全部转入只读Durable固定计划会丢失多轮Planner和文件审批，保留Chat专属调度并共享记录/检查点基础。

## 4. Dependencies

- **DEP-001**: 已部署ChatRun Worker、Agent业务表及aiws_graph原生PG表；无需新建消息队列。

## 5. Files

- **FILE-001**: backend/app/services/chat_tool_runtime.py及agent_graph/sync_tool_graph.py负责恢复。
- **FILE-002**: backend/app/services/chat_run_service.py、chat_run_worker.py负责领取和最终生成。
- **FILE-003**: frontend/src/components/chat-thread.tsx负责重新生成时替换展示。

## 6. Testing

- **TEST-001**: A已提交B中途进程退出，接管后A调用次数为1，B重试有界，最终回答成功。
- **TEST-002**: 取消、旧版本、工具撤销、未知状态和不明写入全部拒绝继续。
- **TEST-003**: 最终回答部分输出后崩溃，重启生成前清空正文、旧片段保留，事件可重放且不拼接。
- **TEST-004**: 保留既有Chat、Tool DAG、Durable、审批、PG和前端回归；真实在线模型与浏览器验证。

## 7. Risks & Assumptions

- **RISK-001**: 外部只读请求已执行但未提交仍可能再次请求，不承诺exactly-once或续接半截Token。
- **RISK-002**: 恢复会重新组装当前授权上下文；已提交工具证据是原时间点快照，不能当成重新核验的实时资料。
- **ASSUMPTION-001**: 业务提交优先于图位置；新租约重新预检，不能直接信任旧内存对象。

## 8. Related Specifications / Further Reading

- docs/216_Chat独立任务与统一取消_2026-11-08.md
- plan/refactor-langgraph-runtime-1.md
