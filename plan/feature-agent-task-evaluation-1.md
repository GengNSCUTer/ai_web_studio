---
goal: 建立按业务场景和执行层次分析的Agent任务评测
version: 1.1
date_created: 2026-11-04
last_updated: 2026-11-05
owner: AI Web Studio
status: In progress
tags: [evaluation, agent, business, trace]
---

# Introduction

![Status: In progress](https://img.shields.io/badge/status-In_progress-yellow)

规模200条Agent任务，区别于已有中英文RAG各100题。先完成12条批量执行/产物检查，再按六类预先标注到200条；逐题结果、执行类型和待完成项分别记录。补题先标注目标、允许副作用和断言，再执行，不用同文件问法翻译凑数。

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
| TASK-001 | 在backend/evals/agent_task_pilot_v1.json标注12条问题、业务资料、事实断言与允许工具，保留200目标配额 | 是 | 2026-11-05 |
| TASK-002 | 在backend/app/services/agent_task_evaluation.py解析扁平Trace、嵌套ToolPlan及跨轮调用，输出逐层计数、条件覆盖率、完成/部分/失败/跳过 | 是 | 2026-11-05 |
| TASK-003 | scripts/evaluate_agent_tasks.py执行真实Chat/RAG及独立Worker文档导出；保存JSON和HTML，结束清理临时资料 | 是 | 2026-11-05 |
| TASK-004 | backend/tests/test_agent_task_evaluation.py覆盖空分母、缺Trace、部分失败、候选漏召回、等待审批及报告内容转义 | 是 | 2026-11-05 |

2026-11-05 执行状态：Phase 1 的批量执行/检查/报告能力已完成，不代表12题全部成功。固定V3.2完整复跑r2为10/12通过、2失败、0跳过，四个文档任务均下载并核验；K01/K02答案和引用正确但规划了多余工具，外部调用被Action Intent拒绝，仍按严格任务失败保留。r1存在四个只读基线快照误判，原始报告保留，不能把修正评分器后的50%→83.33%说成产品提升。Phase 2 扩题前优先修复这两个误规划/无效重规划问题，再按场景扩充，不追加预算或放宽金标准。详情与原始报告见docs/203第7节。

### Implementation Phase 2

- **GOAL-002**: 经逐题审查扩至200条内部业务回归；任务族留出另行建立，不能将本批称为盲测。

| Task | Description | Completed | Date |
|---|---|---|---|
| TASK-005 | 按REQ-001补齐200条不同输入/操作的内部任务；单工具题提供真实必要参数，未知ID查找后读取归多工具 | 是 | 2026-11-05 |
| TASK-006 | 为业务报告、游戏反馈、工单整理、数据核对、论文摘要构建合成资料和目标产物，加入跨文件矛盾、缺失值、重复项及证据不足 | 是 | 2026-11-05 |
| TASK-007 | 首轮编辑9/20；明确替换代码规划修复后原20题r2—r5各20/20，含两条真实重复确认、13条浏览器决策及正文版本回读；在线复杂编辑仍有参数不遵从，完整200未重跑，见docs/206 | 是 | 2026-11-05 |
| TASK-008 | 已建8条真实Chat安全题和12条受控HTTP探针；强制Worker宕机/超时及恢复验证未补齐 | 部分 | 2026-11-05 |

2026-11-05 扩充已执行：原K01/K02工具误选已修复，完整12题r3为10通过/2部分，两题均零Tool调用。200题按30/40/50/40/20/20完整执行：170通过、29部分、1规则失败、0跳过；20份后台文档全部通过，202个临时项目清理。40道知识库题均零Tool调用，但有5题证据/答案检查未满足。审批20题只有9通过，9条审批目标题规划耗尽35秒，另有消息失败和虚构审批状态；重复确认两题未抵达目标分支，不填2/2。F04因拒绝时引用攻击文字被禁词误判，保留原分数并单列人工说明。1026项后端含真实PG/pgvector、产品浏览器、最终200行/20产物链接报告验证通过。原始报告与同批校准统计在`agent-business-200-20261105-r1`，详见docs/205；校准仅将三条拒绝负例排除正向召回分母，不改200条结果。

本计划尚未整体完成：下一步优先编辑规划与真实状态约束、评分器拒绝/引用反例；之后再补强制HTTP超时/Worker宕机恢复、任务族留出、三次稳定性与语义Judge，不继续凭同一开发集成功率宣称泛化。

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
- **FILE-005**: scripts/agent_task_batch_runner.py：真实API/数据库/Worker执行、下载检查、Playwright与定点清理；纯分析服务不获得执行权限。
- **FILE-006**: scripts/build_agent_tasks_200.py、backend/evals/agent_tasks_200_v1.json：执行前标注的六类200题，内部回归而非任务族留出。
- **FILE-007**: scripts/agent_task_approval_runner.py、scripts/agent_task_fault_runner.py：真实浏览器审批和受控HTTP安全异常验证。
- **FILE-008**: docs/205_Agent工具路由修复与200题业务回归_2026-11-05.md：本轮修复、扩题、实际结果与限制。
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
