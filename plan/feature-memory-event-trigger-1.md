---
goal: 长期记忆事件触发、持久缓冲与安全控制请求
version: 1.0
date_created: 2026-11-11
last_updated: 2026-11-11
owner: Codex
status: 'Completed'
tags: [feature, memory, migration]
---

# Introduction

![Status: Completed](https://img.shields.io/badge/status-Completed-brightgreen)

基于用户提供的 2026-11-11 分析报告，改造触发层，复用现有记忆提取图、证据、冲突和启用审核。当前轮实现确定性事件与有界批次；语义重要性模型和周期整合留待后续。

## 1. Requirements & Constraints

- **REQ-001**: 明确记住、稳定偏好、项目决定和更正优先入队；纯通用问题不调用提取模型，暂定/未知陈述缓冲并保留批次兜底。
- **REQ-002**: 扫描游标独立于成功提取游标；缓冲只保存消息 ID、现有来源摘要和范围，不复制用户原文，读取时重新分类。单次扫描最多 50 条、缓冲最多 50 条、提取最多 24 条/12000 字符。
- **REQ-003**: 关闭自动提取时，直接的“记住”仍可建立待审候选；关闭记忆总开关不创建任务，执行前及保存前复核开关。
- **SEC-001**: 引用/外部内容不能作为控制命令；更正不直接覆盖，忘记只对当前范围内唯一精确匹配且版本未变的记忆撤销，歧义交由管理页面确认。
- **CON-001**: 使用 Alembic 显式迁移；生产迁移前备份。不加载 Ollama，不增添新的存储服务；用户于在线补验本轮授权验证通过后提交推送。

## 2. Implementation Steps

### Implementation Phase 1

- **GOAL-001**: 实现触发分类、持久化与原 Worker 接入。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | 在 memory_trigger_policy.py 实现确定性分类及缓冲选择；Conversation 增加扫描/缓冲字段，Job 增加触发原因/优先级/控制结果；迁移 20261111_01 | ✅ | 2026-11-11 |
| TASK-002 | MemoryExtractionJobService 使用会话锁扫描及合并缓冲；MemoryCandidateWorker 复核授权/来源、处理精确撤销和提交后消费缓冲；任务按优先级领取 | ✅ | 2026-11-11 |

### Implementation Phase 2

- **GOAL-002**: 让控制结果可见并完成验证和资料同步。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-003 | MemoryActivityService 和 memory-activity-panel 展示控制请求已完成/待确认；不将控制语句保存为用户事实 | ✅ | 2026-11-11 |
| TASK-004 | 增加顺序对话、编辑删除、并发、权限变化和失败测试，真实 PostgreSQL 与在线模型/浏览器验证；更新 docs 与飞书并回读 | ✅；DeepSeek官方6会话9轮真实链路通过 | 2026-11-11 |

## 3. Alternatives

- **ALT-001**: 每轮新增 LLM 重要性判断：增加延迟及成本，暂不采用。
- **ALT-002**: 仅改触发规则而不持久化缓冲：跳过与暂存无法可靠区分，拒绝。

## 4. Dependencies

- **DEP-001**: 现有 LangGraph 提取图、UserMemoryRepository 用户锁、版本、来源快照及撤销接口。
- **DEP-002**: 当前 PostgreSQL/pgvector 与在线兼容 Provider；不增加数据库或服务。

## 5. Files

- **FILE-001**: backend/app/services/memory_trigger_policy.py、memory_candidate_runtime.py：分类及任务生命周期。
- **FILE-002**: backend/app/models/conversation.py、user_memory.py、repositories/memory_job_repo.py、migrations/versions/20261111_01_记忆事件触发.py：持久状态及迁移。
- **FILE-003**: backend/app/schemas/memory.py、services/memory_activity_service.py、frontend/src/components/memory-activity-panel.tsx：触发及控制结果展示。
- **FILE-004**: backend/tests/test_memory_trigger*.py、test_memory_automation.py、test_memory_postgres_integration.py：策略、状态与并发回归。

## 6. Testing

- **TEST-001**: 固定顺序对话覆盖明确/暂定/引用/临时/敏感/更正/撤销，比较旧计数策略和新策略的调用数与触发时机；不把构造样例当线上成功率。
- **TEST-002**: 并发入队仅一个 Job；模型失败不消费缓冲；来源编辑删除拒绝旧结果；主开关关闭不保存；撤销目标变化不误删。
- **TEST-003**: 相关及全量后端测试、compileall、前端 lint/build、迁移往返、真实在线模型与浏览器通知冒烟。

## 7. Risks & Assumptions

- **RISK-001**: 规则不能理解所有表达；未知陈述保留批次兜底，不宣称语义全覆盖。
- **RISK-002**: 精确忘记匹配不能覆盖自然语言模糊目标；必须显示待确认，不能由模型猜测删除。
- **ASSUMPTION-001**: 沿用用户开启的记忆范围和自动启用选项；不擅自替用户打开开关。

## 8. Related Specifications / Further Reading

- 用户提供 docs/ChatGPT_另外，我觉得在长期记_20261111-1248.pdf。
- docs/163—166、208、214 的历史记忆实现与测试证据；本轮报告以实际验证结果为准。
