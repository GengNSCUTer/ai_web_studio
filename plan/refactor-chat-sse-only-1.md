---
goal: Chat统一持久化Run与SSE订阅，删除NDJSON和纯文本兼容流
version: 1.1
date_created: 2026-11-11
last_updated: 2026-11-11
owner: Codex
status: 'Completed'
tags: [refactor, chat, sse]
---

# Introduction

![Status: Completed](https://img.shields.io/badge/status-Completed-green)

新建、编辑、重新生成和恢复统一使用POST创建Run、GET SSE订阅。删除旧POST流入口、格式选项、Next专用代理和按行JSON解析；保留浏览器断开不取消、独立停止与游标恢复。

## 1. Requirements & Constraints

- **REQ-001**: 对外Chat流仅text/event-stream，不允许format=ndjson或纯文本；创建/查询/取消仍是普通JSON资源接口。
- **REQ-002**: 前端所有生成路径复用subscribeChatRun，原生EventSource负责解析、重连和Last-Event-ID；删除手写fetch读流、after请求与退避，仅保留业务去重和生命周期保护。
- **SEC-001**: 保留JWT/ACL、额度、租约、generation、恢复及独立取消边界；断流只重订阅，不重复创建或调用模型。
- **CON-001**: 保留上一轮未提交记忆改动和用户PDF，不加载Ollama、不改Provider凭据；历史报告保留并标注旧协议。

## 2. Implementation Steps

### Implementation Phase 1

- **GOAL-001**: 清理服务端兼容分支与前端重复读取。

| Task | Description | Completed | Date |
| --- | --- | --- | --- |
| TASK-001 | chat_runs.py只保留SSE，移除chat.py旧路由与main注册；内部事件是对象，测试响应包装只有SSE | ✅ | 2026-11-11 |
| TASK-002 | chat-thread.tsx发送/编辑/重生成使用Run JSON和公共SSE订阅，删除旧Next代理与NDJSON解析 | ✅ | 2026-11-11 |
| TASK-003 | 迁移本地在线/评测脚本至Run资源与共享SSE解析，保留原业务断言 | ✅ | 2026-11-11 |

### Implementation Phase 2

- **GOAL-002**: 验证统一协议与真实用户闭环。

| Task | Description | Completed | Date |
| --- | --- | --- | --- |
| TASK-004 | 后端路由/Worker/持久化与前端分包、重连、取消、错误测试，lint/build/compileall | ✅ | 2026-11-11 |
| TASK-005 | 真实在线API、浏览器新问答/编辑/重生成/断流恢复/停止及最终DB结果，更新本地与飞书 | ✅ | 2026-11-11 |
| TASK-006 | 同日后续：改原生EventSource，验证浏览器自动Last-Event-ID/代理续传/终态关闭，更新文档并按授权交付 | ✅ | 2026-11-11 |

## 3. Alternatives

- **ALT-001**: 仅修改Content-Type但保留按行JSON正文：不符合SSE，拒绝。
- **ALT-002**: 保留POST NDJSON以避免迁移脚本：违背用户清理要求，拒绝。

## 4. Dependencies

- **DEP-001**: 已有ChatRun/事件持久化、独立Worker和after/Last-Event-ID恢复。

## 5. Files

- **FILE-001**: backend/app/api/routes/chat_runs.py、services/chat_stream_service.py、chat_run_worker.py：Run与事件。
- **FILE-002**: frontend/src/lib/chat-run-events.ts、components/chat-thread.tsx：唯一订阅与UI。
- **FILE-003**: scripts/chat_sse_client.py和相关在线/评测脚本：统一客户端。

## 6. Testing

- **TEST-001**: 旧路径404、旧格式422，SSE正文含id/event/data及终态；鉴权和游标隔离不回归。
- **TEST-002**: 中文跨字节/跨帧、CRLF、心跳、去重、快照、有限重连和主动取消。
- **TEST-003**: 真实新问答/重生成/编辑/关闭订阅继续/独立停止，最终正文和状态与DB一致。

## 7. Risks & Assumptions

- **RISK-001**: 旧外部客户端和未迁移脚本将失效，文档提供新接口；不保留隐式兼容。
- **ASSUMPTION-001**: 本轮无需数据库迁移，事件的持久化结构不变。

## 8. Related Specifications / Further Reading

- docs/216_Chat独立任务与统一取消_2026-11-08.md
- docs/224_Chat统一SSE与旧流清理_2026-11-11.md：1372项完整后端、8项前端、真实浏览器/在线模型/独立Worker/PG验证及飞书回读。
- 同日原生后续：9项前端生命周期、lint/build和真实四场浏览器（含独立前端组强杀自动续订）通过，另三场关闭/重开/停止/文件工具回归通过；原1372项是后端未再改动前已执行的全量，不冒称本轮重跑。
