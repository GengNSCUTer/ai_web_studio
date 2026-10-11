---
goal: 将工具候选筛选升级为权限范围内的规则与语义混合召回，并验证改动前后效果
version: 1.0
date_created: 2026-10-11
last_updated: 2026-10-11
owner: AI Web Studio
status: 'Completed'
tags: [upgrade, tools, retrieval, evaluation]
---

# Introduction

![Status: Completed](https://img.shields.io/badge/status-Completed-brightgreen)

修复英文及口语问题漏召回；保留规则快速路径，在线语义调用失败时降级。候选不构造参数，不扩大权限，Planner 和 Executor 仍负责最终规划与执行校验。

## 1. Requirements & Constraints

- **REQ-001**: ToolCandidateSelector.select_async 在已启用、已授权目录内融合规则和 Embedding 排名，最多返回六个候选。
- **REQ-002**: 固定多语言、追问、多意图、新工具和禁用案例，分别报告候选工具组 Recall@6、完整覆盖率、禁止工具泄漏和延迟。
- **SEC-001**: 禁用、Skill 范围与明确文件/网络排除先于语义调用；不向 Provider 发送工具返回、助手指令或凭据。
- **CON-001**: 复用用户现有在线 Embedding 配置；本次不启动 Ollama 模型，不增加数据库结构。
- **GUD-001**: 注释使用中文；缓存容量、超时和输入长度有限；异常 Trace 仅记录固定错误分类。
- **PAT-001**: 先冻结评测输入，再比较 Git 574f457 的旧实现与新实现；改动后重复同一评测，不把候选指标当成端到端成功率。

## 2. Implementation Steps

### Implementation Phase 1

- **GOAL-001**: 固定基线与实现边界。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | backend/evals/tool_candidate_gold_set.json 固定正例、禁止项和等价工具组；scripts/evaluate_tool_candidates.py 加载旧 Git selector 与在线新 selector。 | ✅ | 2026-10-11 |

### Implementation Phase 2

- **GOAL-002**: 接入异步语义召回并保持权限过滤与降级。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-002 | backend/app/services/tools/semantic_retrieval.py 实现配置短快照、有限缓存、向量检查和六秒超时；selector.py 实现先过滤后融合，修复短英文子串和负向元数据误匹配。依赖 TASK-001。 | ✅ | 2026-10-11 |
| TASK-003 | planner.py 使用 select_async；external_context_service.py 传入 user_id，Trace 保留语义通道及降级原因。依赖 TASK-002。 | ✅ | 2026-10-11 |

### Implementation Phase 3

- **GOAL-003**: 完成可复查的真实验收与学习记录。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-004 | backend/tests/test_tool_semantic_selector.py 验证超时、损坏向量、租户/版本隔离、取消和禁止项；运行全量 PostgreSQL 测试。依赖 TASK-003。 | ✅ | 2026-10-11 |
| TASK-005 | scripts/tool_semantic_real_e2e.py 覆盖真实浏览器、在线 Planner、工具、最终回答和持久化；记录评测及局限，同步当前入口与 Day 7。依赖 TASK-004。 | ✅ | 2026-10-11 |

## 3. Alternatives

- **ALT-001**: 继续扩充关键词：无法处理新类别与未列举的同义表达，只作为降级通道。
- **ALT-002**: 每轮增加一次 LLM 初筛：增加规划延迟与输出不确定性，本次使用可缓存的工具向量。

## 4. Dependencies

- **DEP-001**: 现有 KnowledgeEmbeddingService、用户 Embedding 配置与在线 BAAI/bge-m3。
- **DEP-002**: unittest、PostgreSQL 集成环境、Playwright 和 V3.2 在线聊天配置。

## 5. Files

- **FILE-001**: backend/app/services/tools/selector.py、semantic_retrieval.py：召回、边界与缓存。
- **FILE-002**: backend/app/services/tools/planner.py、backend/app/services/external_context_service.py：生产入口。
- **FILE-003**: backend/evals/tool_candidate_gold_set.json、scripts/evaluate_tool_candidates.py、backend/tests/test_tool_semantic_selector.py：评测与回归。
- **FILE-004**: docs/172_工具候选混合召回与验收_2026-10-11.md、Day07 学习记录与飞书入口：真实口径。

## 6. Testing

- **TEST-001**: 相同固定输入对比旧规则、新规则降级、新混合召回；不泄漏禁止项，语义问题候选覆盖改善。
- **TEST-002**: 模拟向量故障及取消、模型/凭证/元数据变化、容量边界；保持单测无外网依赖。
- **TEST-003**: 真实在线 Embedding 与浏览器完整 Chat，校验最终证据及无错误，清理临时数据。

## 7. Risks & Assumptions

- **RISK-001**: 语义召回仍可能误选、漏选；测试集有限且由项目维护者标注，不能宣称通用准确率。
- **RISK-002**: 冷缓存带来额外在线延迟；超时降级和有限缓存降低影响。
- **ASSUMPTION-001**: 用户已配置在线 Embedding；没有可用配置时保留规则路径。

## 8. Related Specifications / Further Reading

[Tool 实现](../backend/app/services/tools/selector.py)、[最新项目入口](../docs/README.md)、[公共资料工具](../docs/171_公共资料MCP与文档Skill接入_2026-10-11.md)。
