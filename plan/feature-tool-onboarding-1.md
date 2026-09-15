---
goal: 为低风险只读 MCP Tool 建立声明式接入、验证、审核与版本失效闭环
version: 1.0
date_created: 2026-09-15
last_updated: 2026-09-15
owner: AI Web Studio
status: 'Completed'
tags: [feature, tool, mcp, onboarding, safety, quality-contract]
---

# Introduction

![Status: Completed](https://img.shields.io/badge/status-Completed-brightgreen)

本计划在阶段 2 的 `ResultQualityContract` 之上，建立新增低风险只读 MCP Tool 的标准接入路径。目标不是让任意远端 JSON 自动获得信任，而是将同类 Tool 的接入收敛为“声明式 canonical Mapper、质量 Profile、脱敏 fixture 验证、人工审核和版本绑定”。每个阶段独立验收，后续阶段不得绕过前一阶段的失败关闭边界。

## 1. Requirements & Constraints

- **REQ-001**: 新增只读 MCP Tool 必须拥有可机器校验的接入合同，至少描述能力身份、运行权限、canonical Mapper、质量合同和 fixture 摘要；缺失关键项时不得进入有效 evidence 链。
- **REQ-002**: 同类返回结构必须可复用已有 `semantic_profile` 与 `profile_mapping`，不得为每个 Provider 复制业务质量 evaluator。
- **REQ-003**: canonical Mapper 必须是受限声明式数据映射；只允许有限 JSON Pointer 读取、固定字段投影、数量/字符上限和脱敏，禁止动态脚本、模板执行、网络调用或 LLM 规则。
- **REQ-004**: 每个待审核 Tool 必须使用脱敏 fixture 覆盖正常结果、缺字段/结构异常和请求不一致（如适用）；可选 `empty` case 对动态远端 MCP 必须验证为失败关闭，因为它不能自称合法空结果；fixture 原始响应不得持久化到用户数据库。
- **REQ-005**: Tool 启用前必须同时满足风险审核、接入合同审核、contract/mapper/schema digest 一致和 fixture 验证通过；任一输入变化都应撤销接入审核。
- **SEC-001**: 本计划不新增 Bash、Shell、SQL、删除文件、任意本机文件写入、支付、邮件、外部发布或任意 HTTP 写能力。
- **SEC-002**: 动态 MCP 返回、远端 metadata、Tool description 与 fixture 内容均为不可信输入；它们不能自称 `empty_answer`、提升权限、改变审批或绕过 Executor。
- **SEC-003**: 接入流程只覆盖 `read_only=true`、`risk_level=low|medium` 的 MCP Tool；高风险或副作用 Tool 必须走独立的审批、幂等和恢复设计，不能通过本计划启用。
- **CON-001**: 保持阶段 2 的质量语义和同步 Chat 上限：`valid/uncertain/invalid`、`evidence/empty_answer/approval_draft` 与最多五轮同步 Workflow 均不扩张。
- **CON-002**: 不将完整 Provider 原始响应、凭据、endpoint 查询参数或包含敏感内容的 fixture 写入 Trace、Artifact、数据库审核记录或 Git。
- **GUD-001**: 按 2.3A → 2.3B → 2.3C → 2.3D 顺序实施；每步完成定向测试、静态检查、文档更新和飞书同步后，再进入下一步。
- **PAT-001**: 使用“合同 → 映射 → fixture 验证 → 审核版本”的分层模式；Profile 决定业务质量，Mapper 只负责安全归一化，Executor 仍是最终权限和 Schema 边界。

## 2. Implementation Steps

### Implementation Phase 1 — 2.3A：接入合同与边界

| Goal | Description | Completed | Date |
|------|-------------|-----------|------|
| GOAL-001 | 定义可校验、可持久化准备的 Tool Onboarding Contract，使后续 Mapper、fixture 和审核流程使用同一份数据模型。 | ✅ | 2026-09-15 |

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | 新增 `backend/app/services/tools/onboarding.py`：定义 `ToolOnboardingContract` 和固定字段集合；实现 `validate_tool_onboarding_contract()`，组合现有 `validate_quality_contract()`，并拒绝非 MCP、非只读、高风险、未知字段、空 profile 映射和未受限 mapper 声明。验证报告对象在 fixture 阶段按实际需要实现，避免过早抽象。 | ✅ | 2026-09-15 |
| TASK-002 | 在 `backend/tests/test_tool_onboarding.py` 覆盖合同的正常只读案例，以及副作用、高风险、动态脚本字段、非法质量合同、缺少 fixture 摘要和非法摘要格式的失败关闭案例。 | ✅ | 2026-09-15 |
| TASK-003 | 新增 `docs/119_阶段2_3A_Tool接入合同与审核边界_2026-09-15.md`，记录接入包字段、信任边界、允许/禁止范围和后续 B/C/D 的输入输出；更新 `docs/04`、`docs/05`、`docs/06`、`docs/07` 和本计划。 | ✅ | 2026-09-15 |

### Implementation Phase 2 — 2.3B：声明式 Canonical Mapper

| Goal | Description | Completed | Date |
|------|-------------|-----------|------|
| GOAL-002 | 让常见 collection / object JSON 形态通过受限映射生成最小 canonical `ExternalSource`，无需为每个同类 Provider 新写 Python Mapper。 | ✅ | 2026-09-15 |

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-004 | 在 `backend/app/services/tools/onboarding.py` 实现 Mapper 合同校验：固定 mapper 类型、payload/item JSON Pointer、title/display/url/score/canonical 字段映射、最多条目数和字符上限；禁止通配写入、表达式、模板和未白名单字段。 | ✅ | 2026-09-15 |
| TASK-005 | 在 `backend/app/services/tools/result_mappers.py` 新增声明式 Mapper 执行入口；只读取 `extract_mcp_payload()` 的结构化 payload，脱敏并裁剪文本后构造最小 `ExternalSource.metadata.raw`，不保留全量原始响应。 | ✅ | 2026-09-15 |
| TASK-006 | 在 `backend/tests/test_tool_onboarding.py` 与 `backend/tests/test_result_mappers.py` 覆盖 object/collection 两种成功形态、字段缺失、非对象 item、超量截断、JSON Pointer 转义、敏感文本脱敏和不允许表达式的失败关闭。 | ✅ | 2026-09-15 |

### Implementation Phase 3 — 2.3C：Fixture 驱动验证

| Goal | Description | Completed | Date |
|------|-------------|-----------|------|
| GOAL-003 | 让新 Tool 在启用前通过可复现的脱敏 fixture 验证，并输出可审计的接入报告，而不是只靠一次真实调用成功。 | ✅ | 2026-09-15 |

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-007 | 在 `backend/app/services/tools/onboarding.py` 实现 fixture bundle 校验与 `evaluate_tool_onboarding_fixture_bundle()`：要求固定 case 名、预期质量状态、预期 Source 数区间和可选请求上下文；bundle digest 绑定合同，fixture body 不得写入持久化状态。 | ✅ | 2026-09-15 |
| TASK-008 | 新增 `backend/scripts/validate_tool_onboarding.py`：读取本地 JSON 接入包和 fixture bundle，执行合同、Mapper、质量门验证，输出脱敏 JSON 报告并以非零退出码表示失败；不读取凭据、不发网络请求。 | ✅ | 2026-09-15 |
| TASK-009 | 在 `backend/tests/test_tool_onboarding.py` 覆盖正常、空/缺字段、请求不匹配、期望不一致和 fixture digest 变化；增加 `docs/120_阶段2_3B_C_声明式Mapper与Fixture验证_2026-09-15.md`。 | ✅ | 2026-09-15 |

### Implementation Phase 4 — 2.3D：动态 MCP 审核与版本绑定

| Goal | Description | Completed | Date |
|------|-------------|-----------|------|
| GOAL-004 | 将动态 MCP Tool 从“风险审核”升级为“风险 + 接入合同 + fixture + 版本 digest”共同审核，任何契约输入变化自动失效。 | ✅ | 2026-09-15 |

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-010 | 扩展 `McpTool` 与启动时迁移：保存受限 onboarding contract JSON、contract digest、fixture digest、onboarding review status、reviewed_at；旧动态 Tool 初始化为未审核且不可启用。 | ✅ | 2026-09-15 |
| TASK-011 | 扩展 `backend/app/schemas/tool_config.py` 与 `backend/app/api/routes/tools.py`：只允许通过合同/fixture 校验的低风险只读 Tool 提交审核；更新 Schema、输出 Schema、描述、固定参数、风险、Mapper/Profile 或 fixture digest 时撤销审核并停用 Tool。 | ✅ | 2026-09-15 |
| TASK-012 | 调整 `backend/app/services/tools/catalog.py`：仅加载 `risk_reviewed + onboarding_reviewed + digest` 一致的动态 MCP Tool，并将已审核合同的 Mapper/Profile 注入 `ToolDefinition`；否则保留诊断可见性但保持失败关闭。 | ✅ | 2026-09-15 |
| TASK-013 | 扩展 `frontend/src/lib/types.ts` 与 `frontend/src/components/settings-center.tsx`：展示“待配置 / fixture 未通过 / 待审核 / 已审核 / 已失效”状态和 contract digest，不提供自动审批；新增前端 lint/build 与后端 API、Catalog、失效回归测试。 | ✅ | 2026-09-15 |

## 3. Alternatives

- **ALT-001**: 让动态 MCP 的 generic JSON 一旦非空就直接成为 evidence。未选择，因为结构合法不代表业务有效，且会绕过阶段 2 的 Profile 和信任边界。
- **ALT-002**: 用 LLM 在运行时阅读任意 JSON 并判断是否可用。未选择，因为输出不稳定、不可审计且容易受 Prompt Injection 影响；LLM 最多可离线生成接入合同草稿，不能成为质量门。
- **ALT-003**: 为每个新 Provider 手写 Python Mapper。未选择，因为同类 Tool 的字段差异会造成重复代码；保留专用 Mapper 仅用于高德路线、审批草案等复杂跨字段语义。
- **ALT-004**: 将用户真实 Tool 测试响应直接存入数据库作为 fixture。未选择，因为响应可能含正文、隐私数据、endpoint 或敏感字段；只保留脱敏 fixture digest 和验证报告摘要。

## 4. Dependencies

- **DEP-001**: 阶段 2 的 `validate_quality_contract()`、`semantic_profile`、`profile_mapping` 和 `evaluate_tool_result_quality()`。
- **DEP-002**: `ToolAdapterRunner`、`extract_mcp_payload()` 与 `map_mcp_result()` 的既有 MCP 协议处理。
- **DEP-003**: `McpTool`、`ToolConfigRepository`、工具设置 API、启动时 SQLite/PostgreSQL 兼容迁移和风险审核流程。
- **DEP-004**: `AGENTS.md` 的中文注释、测试、文档、飞书同步和 GitHub 提交约束。

## 5. Files

- **FILE-001**: `backend/app/services/tools/onboarding.py` — 接入合同、声明式 Mapper 合同和 fixture 验证的唯一入口。
- **FILE-002**: `backend/app/services/tools/result_mappers.py` — 执行受限声明式 canonical Mapper。
- **FILE-003**: `backend/app/models/tool_config.py` — 动态 MCP Tool 审核与 digest 持久化字段。
- **FILE-004**: `backend/app/core/startup.py` — 旧库增量字段迁移与安全默认值。
- **FILE-005**: `backend/app/api/routes/tools.py` 与 `backend/app/schemas/tool_config.py` — 接入包提交、验证和审核状态 API。
- **FILE-006**: `backend/app/services/tools/catalog.py` — 只向 Planner 暴露完整审核且 digest 一致的动态 Tool。
- **FILE-007**: `backend/tests/test_tool_onboarding.py` — 接入合同、Mapper、fixture 和审核失效回归。
- **FILE-008**: `backend/scripts/validate_tool_onboarding.py` — 不联网的本地 fixture 验证脚本。
- **FILE-009**: `docs/119_阶段2_3A_Tool接入合同与审核边界_2026-09-15.md`、`docs/120_阶段2_3B_C_声明式Mapper与Fixture验证_2026-09-15.md`、`docs/121_阶段2_3D_动态MCP审核与版本绑定_2026-09-15.md` — 可执行接入与审核说明。

## 6. Testing

- **TEST-001**: 2.3A 合同校验测试必须证明非 MCP、非只读、高风险、缺质量合同、动态 mapper 字段和非法 fixture 摘要均失败关闭。
- **TEST-002**: 2.3B Mapper 测试必须证明只投影白名单 canonical 字段，结构损坏和敏感内容不进入有效 Source。
- **TEST-003**: 2.3C fixture 验证必须在不联网、不读取凭据的条件下，对正常、可选空、缺字段和不匹配响应输出确定性报告；动态远端 MCP 的空结果只能得到失败关闭结果。
- **TEST-004**: 2.3D 动态 MCP 回归必须证明缺审核、合同/fixture/schema digest 变化或风险变化时，Tool 不进入 Candidate Catalog。
- **TEST-005**: 每个阶段至少运行定向 `unittest`、`compileall`、manifest JSON 校验和 `git diff --check`；2.3D 有前端改动时运行 ESLint、生产 build 和 Playwright 冒烟。

## 7. Risks & Assumptions

- **RISK-001**: 声明式 Mapper 若允许任意复杂路径或模板，会重新引入脚本执行、资源耗尽和 Prompt Injection 面；因此映射类型、路径、数量和文本长度必须固定上限。
- **RISK-002**: Profile 复用不代表业务语义完全相同；无法用证据/身份/集合/请求匹配表达的跨字段规则必须保留专用 Mapper 或 evaluator。
- **RISK-003**: 用户可能把含敏感内容的真实响应当 fixture 上传；2.3C 只使用本地脱敏样本与摘要，2.3D 不持久化原始 fixture body。
- **ASSUMPTION-001**: 当前动态 MCP 仍仅支持远程 Streamable HTTP 和用户 ACL/SSRF 策略，未引入本地 stdio MCP。
- **ASSUMPTION-002**: 本计划只覆盖单 Tool 的接入审核；Skill 安装、长任务 Durable handoff、跨 Tool DAG 部分成功聚合仍按现有独立路线推进。

## 8. Related Specifications / Further Reading

- [阶段 2 执行状态与质量合同计划](design-tool-execution-state-semantics-1.md)
- [阶段 2.2A 声明式语义 Profile](../docs/117_阶段2_2A_声明式语义Profile框架_2026-09-14.md)
- [阶段 2.2B/C Provider Mapper 与请求一致性](../docs/118_阶段2_2B_C_真实Provider映射与请求一致性_2026-09-14.md)
- [项目动态 MCP 与安全设计](../docs/06_技术实现设计.md)
