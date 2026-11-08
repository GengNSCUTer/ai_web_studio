---
goal: 普通Chat持久化任务、订阅重连与统一取消
version: 1.0
date_created: 2026-11-08
last_updated: 2026-11-08
status: 'Completed'
tags: [chat, worker, streaming, cancellation]
---

# Introduction

![Status: Completed](https://img.shields.io/badge/status-Completed-brightgreen)

将生成从HTTP生命周期移到独立Worker，保留现有LangGraph、Executor和消息generation控制。数据库保存ChatRun与单调递增事件；SSE/NDJSON只读取，不拥有生成任务。

## 1. Requirements & Constraints

- **REQ-001**: 创建Run与消息骨架同事务；重复client_request_id只返回原任务，内容不同返回409。
- **REQ-002**: 浏览器关闭不取消；以run_id读取快照、after或Last-Event-ID重放；主动停止POST cancel。
- **REQ-003**: queued/running/cancel_requested/succeeded/cancelled/failed/expired/interrupted明确；终态不可被迟到生成覆盖。
- **REQ-004**: 每次事件/正文/终态提交复核owner、lease_version、有效期、generation；用户取消与完成以数据库锁顺序裁决。
- **REQ-005**: 默认用户未完成Run上限2、全局领取上限16、Worker本地并发4、排队5分钟、整次15分钟、输出8192Token及20万字符、事件7天清理；参数可配置。
- **SEC-001**: 所有读/取消/订阅验证当前用户与会话；不持久化API Key、Session或客户端。保留工具授权/审批/SSRF/原意图检查。
- **CON-001**: 不自动重试已经开始的生成或工具；Worker崩溃按租约标interrupted，保留已提交片段；不声称token续接或exactly-once。
- **PAT-001**: 复用原配额锁、共享租约判断和心跳循环基础、Provider池及Chat图；独立Chat状态，不伪装成Tool Step。

## 2. Implementation Steps

### Implementation Phase 1

- **GOAL-001**: 完成持久化、领取、事件与取消。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | models/chat_run.py、显式20261108_01迁移；services/chat_run_service.py原子创建/快照/事件/取消/失租约收口 | ✅ | 2026-11-08 |
| TASK-002 | services/runtime_lease.py提取共享租约保护；chat_run_worker.py使用独立Session、心跳、限时/Token/事件上限，复用Chat图和流式收口 | ✅ | 2026-11-08 |
| TASK-003 | api/routes/chat_runs.py创建/重生成/编辑、快照、会话最新Run、取消、SSE/NDJSON游标重放；旧流入口接入同一任务 | ✅ | 2026-11-08 |

### Implementation Phase 2

- **GOAL-002**: 完成前端订阅恢复与部署。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-004 | 前端代理创建后订阅，传x-chat-run-id；chat-thread主动停止调用cancel，重开会话自动订阅活跃Run，不把断网标成任务失败 | ✅ | 2026-11-08 |
| TASK-005 | 独立run_chat_worker脚本与Supervisor、健康探针、配置示例和事件保留清理 | ✅ | 2026-11-08 |

### Implementation Phase 3

- **GOAL-003**: 真实闭环验收、审查和资料同步。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-006 | PG并发重复创建/取消完成竞争/旧Worker/过期/事件游标/跨用户；可控Provider取消/迟到/无透明重试测试 | ✅ | 2026-11-08 |
| TASK-007 | 在线模型生成接收至少100Token后关闭浏览器，完成后重开验证完整正文且调用一次；主动停止/新发/浏览器错误检查 | ✅ | 2026-11-08 |
| TASK-008 | 全量PG/Redis、前端lint/build/浏览器、部署检查，docs/04—07/216/README及飞书同步，保留真实边界 | ✅ | 2026-11-08 |

完成证据：业务提交c111346，Actions42454572211前后端成功，1231项后端中1230通过/1缺服务器论文PDF跳过（本地补测通过），PG/Redis实际执行；前端lint/build/Node4项通过。严格浏览器r4实际可见146Token后关页/Web重启，后台17,288字符正常stop并回读全文，最终生成逻辑尝试1；HTTP最终冒烟和真实Worker停止/恢复健康探针通过，原r3结尾标记失败保留。详见docs/216及backend/evals/results/chat-run-*20261108*。

## 3. Alternatives

- **ALT-001**: Web create_task不提供独立进程执行与重启状态，故不使用。
- **ALT-002**: 把流式生成当Tool Step会混淆重试、事件与审批，故只共享基础设施。

## 4. Dependencies

- **DEP-001**: 使用当前PostgreSQL35433、前端32008/API32007与backend/.venv；显式备份/迁移后部署唯一服务。
- **DEP-002**: 阶段2依赖阶段1；阶段3验证两者；在线模型沿用DeepSeek-V3.2，不启动Ollama。

## 5. Files

- **FILE-001**: backend/app/models/chat_run.py、services/chat_run_service.py、chat_run_worker.py、runtime_lease.py。
- **FILE-002**: backend/app/api/routes/chat.py、chat_runs.py及前端chat-stream-proxy/chat-thread。
- **FILE-003**: backend/tests/test_chat_runs.py、test_chat_runs_postgres.py、scripts/chat_run_real_e2e.py与deploy/supervisord.conf。

## 6. Testing

- **TEST-001**: 保存事件与Message同事务、终态只一次、页游标不重复、过期事件返回明确提示。
- **TEST-002**: 取消连点、完成竞争、超时、Worker优雅退出/强制退出、旧租约迟到、重新发送与越权。
- **TEST-003**: 浏览器关闭后独立Worker完成，重连不增加模型尝试；断网只结束订阅，不将Run取消。

## 7. Risks & Assumptions

- **RISK-001**: 网络断开仍计费；用任务时限/Token/配额控制，不以浏览器在线人数计并发。
- **RISK-002**: 第三方可能已执行不能撤回；取消阻止新的推进和迟到提交，不承诺撤销已发生动作。
- **ASSUMPTION-001**: 保留新旧传输接口的使用兼容，但所有正式流入口由相同持久化Run承接。

## 8. Related Specifications / Further Reading

- docs/215_LangGraph迁移完成说明与最终验收_2026-11-06.md
- https://html.spec.whatwg.org/multipage/server-sent-events.html
