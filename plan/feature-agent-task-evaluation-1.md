---
goal: 建立按业务场景和执行层次分析的Agent任务评测
version: 1.0
date_created: 2026-11-04
last_updated: 2026-11-04
owner: AI Web Studio
status: In progress
tags: [evaluation, agent, business, trace]
---

# Introduction

![Status: In progress](https://img.shields.io/badge/status-In_progress-yellow)

目标规模200条Agent任务，区别于已有中英文RAG各100题。本轮交付统一结果分析、12条先行任务及真实业务交付试跑；不把目标规模写成已经执行的数量。后续补题先标注目标、允许副作用和断言，再执行，不用同文件问法翻译凑数。

## 1. Requirements & Constraints

- **REQ-001**: 目标分布为无需工具30、单工具40、多工具50、知识与证据40、文件编辑审批20、故障安全20；每项主类别唯一，业务标签可多选。
- **REQ-002**: 每题包含任务输入、资料/版本、必需工具等价组、禁止工具、目标产物、事实断言、审批/副作用断言和执行方式。
- **REQ-003**: 指标分别计算Selector、Planner、Executor/Workflow、最终任务、耗时/可观测Token；提供分子/分母，缺少数据为null，不填0或从失败分母删除。
- **REQ-004**: Planner能力覆盖率以候选完整覆盖样本为条件；依赖与参数单列，不把工具组覆盖冒充完整规划正确率。
- **REQ-005**: 实际产物必须下载并检查正文/格式、数据库阶段与会话回传；HTTP200/done/工具success均不能代替业务完成。
- **SEC-001**: 只使用临时项目和明确授权资料；不开放Shell、SQL、任意路径写入或真实工单写接口；沙箱不可用仍拒绝脚本运行。
- **CON-001**: 不修改在线Provider/用户配置，不启动Ollama，不从报告输出凭据或审批令牌。
- **CON-002**: 合成数据、固定输入文档和真实用户任务分别标记。对照/开发/留出按任务族拆分，改答案或规则后不能仍称盲测。
- **PAT-001**: 复用ToolRouteRun/ToolCallRun/ChatRuntimeMetric和Run/Step/Artifact事实，不另建重复执行账本。

## 2. Implementation Steps

### Implementation Phase 1

- **GOAL-001**: 用当前能力交付可复跑先行集与统一离线报告。

| Task | Description | Completed | Date |
|---|---|---|---|
| TASK-001 | 在backend/evals/agent_task_pilot_v1.json标注12条问题、业务资料、事实断言与允许工具，保留200目标配额 | 进行中 | 2026-11-04 |
| TASK-002 | 在backend/app/services/agent_task_evaluation.py解析扁平Trace、嵌套ToolPlan及跨轮调用，输出逐层计数、条件覆盖率、完成/部分/失败/跳过 | 进行中 | 2026-11-04 |
| TASK-003 | scripts/evaluate_agent_tasks.py执行真实Chat/RAG及独立Worker文档导出；保存JSON和HTML，结束清理临时资料 | 进行中 | 2026-11-04 |
| TASK-004 | backend/tests/test_agent_task_evaluation.py覆盖空分母、缺Trace、部分失败、候选漏召回、等待审批及报告内容转义 | 进行中 | 2026-11-04 |

### Implementation Phase 2

- **GOAL-002**: 经逐题审查扩至200条，并冻结任务族留出。

| Task | Description | Completed | Date |
|---|---|---|---|
| TASK-005 | 按REQ-001补齐独立任务；单工具题提供真实必要参数，未知ID查找后读取归多工具；禁止用换城市/翻译堆满配额 | 否 | 待执行 |
| TASK-006 | 为业务报告、游戏反馈、工单整理、数据核对、论文摘要各构建独立资料和目标产物，加入跨文件矛盾、缺失值、重复项及证据不足 | 否 | 待执行 |
| TASK-007 | 编辑20题覆盖预览/批准/拒绝/旧版本/重复确认/只读权限，复用真实浏览器脚本，不用构造成功状态代替点击 | 否 | 待执行 |
| TASK-008 | 安全故障20题用受控HTTP/独立Worker故障注入，逐项记录注入是否成立、分支保留、幂等恢复与越权拒绝 | 否 | 待执行 |

### Implementation Phase 3

- **GOAL-003**: 固定版本执行与回归，避免为了指标变更任务。

| Task | Description | Completed | Date |
|---|---|---|---|
| TASK-009 | 预先固定expected_dependencies/参数语义断言与事实金标准；随机交错比较版本，每题默认3次重复，单次及重复稳定率同时展示 | 否 | 待执行 |
| TASK-010 | 标注每个可核实claim和来源，规则检查加独立模型判断、人工抽查；保留Judge失败与原答案，不把引用合法当语义支持 | 否 | 待执行 |
| TASK-011 | 将已审核的结构化离线结果接入只读评测页面；长期线上监控另行定义采样窗口、权限和保留期 | 否 | 待执行 |

## 3. Alternatives

- **ALT-001**: 只展示一个200题总成功率；拒绝，因为掩盖安全失败、候选遗漏与最终产物失败。
- **ALT-002**: 立即增加脚本执行、工单平台写入和新Tool市场；本轮不采纳，先评现有能力，不用新增权限替代评测。
- **ALT-003**: 所有正确性用LLM评分；不采纳，文件、版本、权限、数字计算优先客观断言，语义分另列。

## 4. Dependencies

- **DEP-001**: 当前PostgreSQL、Redis、Backend与DurableWorker，在线DeepSeek-V3.2及账号已授权工具。
- **DEP-002**: 现有RAG百题只复用题目和原文依据，不复用历史得分；知识库不存在/版本不匹配必须显示未执行。

## 5. Files

- **FILE-001**: backend/evals/agent_task_pilot_v1.json：先行任务及合成业务资料。
- **FILE-002**: backend/app/services/agent_task_evaluation.py：纯评测分析与HTML输出，无工具执行权限。
- **FILE-003**: scripts/evaluate_agent_tasks.py：测试驱动、资料清理与报告输出。
- **FILE-004**: docs/203_Agent分层评测与业务闭环计划_2026-11-04.md：当前证据、差距、扩题方式与复现。

## 6. Testing

- **TEST-001**: 逐层指标测试必须保留分母与不适用项，候选未覆盖不计入条件Planner分母，但任务仍计失败。
- **TEST-002**: 有done但事实不满足、缺产物、非法引用或越权副作用均不能通过；安全约束失败不能被其它得分抵消。
- **TEST-003**: 真实下载Markdown/Word，检查生成阶段、文件格式、保留事实、会话回传和未静默保存工作区；重试/进程恢复单列受控故障批次。

## 7. Risks & Assumptions

- **RISK-001**: 固定小资料可能过易；必须用留出任务和不同资料长度验证泛化，不能推广为线上成功率。
- **RISK-002**: 真实外部服务波动改变耗时和答案；保留版本、运行时间、错误类型和原始证据，不能删异常样本。
- **RISK-003**: 明确关键词断言只核实有限事实，不能证明整篇报告无幻觉；完整语义评分为后续工作。
- **ASSUMPTION-001**: 工单首版是分类/待办文件导出，不是远端系统创建；游戏反馈来自合成数据，不是上线游戏业务；结构化分析为小表核对，不是沙箱执行验收。

## 8. Related Specifications / Further Reading

[200预算实验](../docs/200_同步Tool预算真实实验_2026-11-04.md)、[201编辑预览](../docs/201_编辑提案发现规划与完成修复_2026-11-04.md)、[202确认写入](../docs/202_文件确认写入真实浏览器验收_2026-11-04.md)、scripts/evaluate_tool_tasks.py、scripts/document_task_real_acceptance.py。
