---
goal: 用可回退的摘要版本和可靠分批重建，改善历史删改后的上下文缺失及重复压缩成本
version: '1.0'
date_created: '2026-11-17'
last_updated: '2026-11-17'
owner: AI Web Studio
status: 'Completed'
tags: [context, summary, recovery, interview]
---

# Introduction

![Status: Completed](https://img.shields.io/badge/status-Completed-brightgreen)

2026-11-17已完成实现、数据库备份/迁移、Worker托管、全量回归及真实在线模型/浏览器验证。全量1488、收尾134、最后34项及2项停机专项通过，前端lint/build/12项通过；真实Worker重启后同一Job复用已提交批次继续成功。飞书当前入口、技术架构、产品方案和上下文文档已同步回读。不是重新建设长期记忆，也不把LangGraph执行检查点当作历史摘要版本。实际实现与限制以[237](../docs/237_滚动摘要版本回退与持续重建_2026-11-17.md)和[验收记录](../backend/evals/results/summary-rebuild-20261117.json)为准。

实施收敛：批次Token选择放在统一摘要服务和正式Chat组装入口，不重复塞入纯预算类；缺口统一转后台并明确要求稍后重新生成，无缺口继续原增量摘要，未新增“判断问题是否关联旧历史”的模型调用。来源修订号仅在影响摘要/修复目标的变更时递增，近期改动保留正在执行的修复。进度通过原状态接口查询，不新增前端轮询；前端快照保留恢复提示。

改造前删除摘要覆盖范围内的消息，会在同一事务清空整份摘要。旧结果写回受到历史复核和会话行锁保护，正确性基础已保留。原缺口是：清空后每轮只推进一个有界批次，前台可能在重建未追上时用不完整上下文回答。

隔离复现：1000条历史、摘要原覆盖前900条，删除第200条。下一轮读取72条（最早48条及最新24条），一次刷新只覆盖到第48条，另有927条既未进入本次摘要也未作为历史读取。使用SQLite临时库和受控摘要模型返回，未访问用户会话，不属于在线答案质量评测。现有55项测试通过，含真实隔离PostgreSQL生命周期测试；说明原安全行为成立，不表示恢复缺口已解决。

推荐次序：先检测和修复历史缺口，再保存可回退摘要版本并完成后台重建；独立分段只保留为后续选择。不要让模型从混合后的旧摘要中猜测删除哪些事实。

## 1. Requirements & Constraints

- **REQ-001**: 摘要、覆盖边界、修复任务状态必须与历史删改在同一数据库事务中更新；模型等待期间不得持有会话锁或数据库事务。
- **REQ-002**: 保留有界读取。每批最多48条，近期窗口24条；额外使用模型输入Token预算限制每批正文，不一次载入全部会话。
- **REQ-003**: 前台必须区分“摘要已追上近期窗口”和“仍有中间缺口”。不能把边界推进正确误称为全部历史已覆盖。
- **REQ-004**: 删除、批删、编辑重答、重生成及后台结果消息改写均触发相同的版本失效规则。最近窗口内、未影响摘要前缀的改动不废弃有效摘要版本。
- **REQ-005**: 摘要版本只可回退到最早受影响消息之前。累积摘要的后续版本均已依赖被改内容，必须一起失效，不能仅改一个版本继续使用后续版本。
- **REQ-006**: 已有会话没有历史摘要版本时允许从头重建，不把现存最新摘要倒推出虚假的历史版本。
- **SEC-001**: 重建仍限当前用户和会话；日志只记录边界、计数、耗时和错误码，不记录摘要原文、凭据或完整消息。
- **CON-001**: 沿用现有`created_at/sequence/id`排序，兼容sequence为空。当前sequence由MAX+1生成，尾部删除后可能复用，不能直接将sequence当作永久唯一历史坐标。
- **CON-002**: 不增加来源内容哈希。数据库版本号、会话行锁、事务内失效及实际源消息复核处理已知竞争；手工绕过业务入口直接改库不在保证范围。
- **CON-003**: 普通摘要仍是有损压缩，覆盖完整不等于所有事实保留或答案必然正确。规则摘录回退须标明降级，不得冒充同质量的模型重建。
- **GUD-001**: 优先修复恢复链；不同时引入独立分段、全局摘要树、长期记忆重构或新的Redis队列。

## 2. Implementation Steps

### Implementation Phase 1

- **GOAL-001**: 显式识别摘要缺口，使重建进度和前台实际使用内容一致。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | `MessageRepository.list_for_context/freeze_context_history`保留through锚点、最早连续批次及`has_backlog/pending_count`；额外查询一条判断48条后是否仍有积压，不加载剩余正文。 | ✅ | 2026-11-17 |
| TASK-002 | `chat_context_assembly_service.py`用当前数据库窗口重新检查边界与近期窗口连续性；通过原`SummaryRefreshBundle.stats`传递source_count、remaining_backlog、summary_mode和repair状态，未另增闲置Runtime字段。 | ✅ | 2026-11-17 |
| TASK-003 | 在统一摘要服务的`select_summary_batch`及Chat组装入口按Token选择连续完整批次，`build_summary_source_text`不再截1800字。超长单条用Job偏移分片，全部处理后才推进整条边界；纯预算类保留现有职责。 | ✅ | 2026-11-17 |
| TASK-004 | 在`backend/tests/test_context_summary_lifecycle.py`及`test_chat_memory_capacity.py`增加1000条删早期、删边界、删近期、三种预算模式、超长单条尾部事实及规则回退的复现。明确缺口检测结果，不把“摘要到48”当作“已恢复900”。 | ✅ | 2026-11-17 |

Phase 1依赖次序：TASK-001 → TASK-002；TASK-003与前两项可独立开发，TASK-004最后验收。此阶段只提供诊断，不宣称后台恢复已完成。

### Implementation Phase 2

- **GOAL-002**: 保存有限的摘要版本，历史改动后回退到安全前缀。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-005 | 新增`backend/app/models/conversation_summary.py:ConversationSummaryCheckpoint`及Alembic迁移，保存会话ID、摘要正文、边界ID与排序三元组、生成方式、源修订号、创建时间；每次成功刷新同事务保存，按会话保留最近32份，删除会话时级联清除。 | ✅ | 2026-11-17 |
| TASK-006 | `Conversation.context_history_revision`及`conversation_summary_service.py`统一管理修订号/版本。REQ-004入口持会话锁确定最早变更位置，影响摘要/修复目标才递增修订号；近期无关改动不重启Job。版本用数据库主键，不新增内容哈希。 | ✅ | 2026-11-17 |
| TASK-007 | `MessageRepository.invalidate_summary_for_changes`委托摘要服务：删除受影响位置及以后的摘要版本，恢复最后一个严格早于该位置的版本；无可用版本则清空。失效版本直接删除，不留下可误用的“历史正文”。当前摘要与版本删除/回退都由调用者一起commit或rollback。 | ✅ | 2026-11-17 |
| TASK-008 | 测试1000条、版本覆盖200/400/600/800/900，删除第750条应恢复600；删除第200条不能恢复覆盖200的版本。覆盖批删最早位置、同ID改正文、边界丢失、旧会话无版本、保留上限和事务回滚。 | ✅ | 2026-11-17 |

Phase 2依赖Phase 1。TASK-005 → TASK-006 → TASK-007 → TASK-008；模型注册、迁移和schema验证必须同批完成。

### Implementation Phase 3

- **GOAL-003**: 后台持续补齐摘要，前台明确处理未恢复历史，最后验证真实模型。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-009 | 在`conversation_summary.py`增加独立摘要重建Job及迁移，字段含会话/修订号、目标历史锚点、已提交游标、状态、attempts/next_attempt_at、lease_owner/version/expires_at及错误码。会话锁下合并同修订的活跃任务，删改与创建任务一起提交；不复用长期记忆提取Job语义，也不把摘要塞进Tool Step。 | ✅ | 2026-11-17 |
| TASK-010 | 新增`backend/app/services/summary_rebuild_worker.py`及`backend/scripts/run_summary_worker.py`，复用`runtime_lease.py`的心跳/持有权判定和现有Supervisor管理方式。领取短事务→读取最多48条/Token预算批次→释放事务→调用模型→重锁并复核源修订号、游标及持有权→保存摘要版本与进度同事务提交。固定历史目标不追逐新消息；成功后继续下一批，最多三次瞬态重试，失败保留已提交进度。 | ✅ | 2026-11-17 |
| TASK-011 | Chat无中间缺口且当前批次可容纳时沿用原增量摘要；存在积压/超长分片/活跃Job时，提交持久化Job并以`context_summary_rebuilding`提示稍后重新生成。ChatRun保留具体错误码，SSE先错误说明后终态，页面重开恢复提示，不修改已返回HTTP202；未启用模型关联判断或自动唤醒旧Chat。 | ✅ | 2026-11-17 |
| TASK-012 | 补充真实PG多Worker争抢、删改与提交并发、旧租约迟到、完成后重复领取、进程重启从已提交游标继续和取消/删除会话测试。通用摘要Provider失败时保留repair失败状态和安全前缀，不把规则回退当成高质量修复完成。 | ✅ | 2026-11-17 |
| TASK-013 | 使用固定测试账号和当前在线Provider构造三种长度会话；浏览器删除已覆盖消息后观察恢复提示、后台进度与最终回答。记录重建调用数、输入/输出Token、耗时、旧事实残留和仍有效事实问答，不启动Ollama；临时会话与Job清理，不改现有用户资料。 | ✅ | 2026-11-17 |
| TASK-014 | 完成后更新`docs/159`、`160`、`161`、`162`及178演进说明，定点同步飞书并回读；实现和验收完成前保持本计划Planned，不把本轮55项回归当作新方案通过。 | ✅ | 2026-11-17 |

Phase 3依赖Phase 2。TASK-009 → TASK-010 → TASK-011 → TASK-012 → TASK-013 → TASK-014。Phase 1—3整体验收后才宣称“可靠重建已完成”；不单独开放仍会静默缺失历史的中间态。

## 3. Alternatives

- **ALT-001**: 保留每次整份清空、不做恢复。已验证不会安全复用脏摘要，但长历史恢复缺口和压缩成本不符合目标。
- **ALT-002**: 让LLM从最新旧摘要中删除受影响事实。无法可靠追踪多轮融合的来源，且容易残留，不采用。
- **ALT-003**: 独立分段摘要＋全局汇总。删除早期消息时主要重建对应段，更符合频繁局部编辑；但当前还需调整跨段指代、来源范围、全局汇总依赖及Prompt。先记录回退方案真实成本，若早期编辑成本仍高再做，不承诺检查点已实现局部重建。
- **ALT-004**: 每轮在前台循环到全部追上。可能把几十次模型请求放在Chat准备阶段，造成超时及Worker占用，不采用。

## 4. Dependencies

- **DEP-001**: 沿用PostgreSQL、SQLAlchemy短事务、现有会话行锁、显式Alembic迁移和数据库结构验证，不新增外部存储。
- **DEP-002**: 沿用`ChatProviderService`的当前账号Provider配置、Token估算器及`runtime_lease.py`，不把模型密钥存入Job。
- **DEP-003**: Job通过独立Worker轮询数据库，不需要Redis消息；Supervisor仅在部署本功能时增加一个新职责，不改变现有数据库或重复启动其他服务。

## 5. Files

- **FILE-001**: `backend/app/repositories/message_repo.py`、`backend/app/services/message_service.py`：有界历史元数据、同事务历史变更和摘要失效入口。
- **FILE-002**: `backend/app/services/chat_context_assembly_service.py`、`context_governance_service.py`、`chat_assembly_runtime.py`、`chat_execution_models.py`：摘要输入、覆盖进度、前台降级及诊断。
- **FILE-003**: `backend/app/models/conversation.py`、拟新增`conversation_summary.py`、`backend/app/models/__init__.py`、`backend/migrations/versions/`：修订号、摘要版本、重建任务及迁移。
- **FILE-004**: 拟新增`backend/app/services/conversation_summary_service.py`、`summary_rebuild_worker.py`、`backend/scripts/run_summary_worker.py`：安全回退、持续重建、租约和恢复。
- **FILE-005**: `backend/app/services/durable_tool_runtime.py`、`document_task_service.py`：既有后台结果消息改写路径复用同一失效服务。
- **FILE-006**: `backend/app/services/chat_run_worker.py`、前端现有Chat错误/状态展示及部署Supervisor配置：恢复提示、可重试状态及Worker运维；按实际错误事件消费位置最小修改，不新增协议。
- **FILE-007**: `backend/tests/test_context_summary_lifecycle.py`、`test_chat_memory_capacity.py`、`test_prompt_context_governance.py`及拟新增`test_summary_rebuild_worker.py`：正确性、容量及故障验收。

## 6. Testing

- **TEST-001**: 删除已覆盖/边界/近期消息、批删混入其他会话ID、同ID编辑、重生成和后台结果变更，均检查摘要、版本和Job在同一事务提交或回滚。
- **TEST-002**: 1000及10000条历史恢复至近期窗口前，逐批ID不重不漏；新消息追加不扩张已冻结目标，尾部删除和旧sequence为空不破坏顺序。
- **TEST-003**: 模型等待期间无数据库事务；等待中删消息、同时两个摘要写回、旧租约及重复Job提交不能覆盖新版本。
- **TEST-004**: 超长消息的尾部事实未静默丢失；Token预算控制不把未读文本标作已覆盖；规则降级有独立诊断，不证明事实保留率。
- **TEST-005**: 后台重启复用已提交批次；超时有限重试并保留进度；没有版本时从头重建、有安全版本时不重读该前缀。
- **TEST-006**: 浏览器收到恢复中提示且不展示虚构历史回答；恢复后能回答保留事实。检查删除事实是否残留时区分摘要直接来源与仍存在的后续消息，不能拿仍含相同内容的其他消息误判。

## 7. Risks & Assumptions

- **RISK-001**: 累积版本主要节省改动位置之前的成本，删除最早内容仍可能全量重建；32份保留上限也限制可回退距离，必须在报告中说明。
- **RISK-002**: 摘要有损；逐批成功、事实保留率、最终问答效果是三类指标，不能以边界正确代替答案质量。
- **RISK-003**: 消息删除不自动清理基于它产生的长期记忆、报告和其他产物；本阶段只保证后续摘要的来源与失效，不能承诺全系统遗忘。
- **RISK-004**: 所有能改写消息正文的业务入口必须经过统一服务；漏掉入口会破坏修订号保护，需全仓调用审查和真实PG测试。
- **ASSUMPTION-001**: 当前主要需求是降低偶发删改成本并保证重建不缺段；尚无频繁编辑长会话的生产分布，不据此立即升级独立分段架构。

## 8. Related Specifications / Further Reading

- 用户提供讨论：`/disk2/gengnan/home/.codex/attachments/7c015cac-a7f0-421d-b0d2-25213b38664c/Pasted text.txt`。其中示意数据和建议不是已实现功能。
- [178：历史变更与并发写回修复](../docs/178_滚动摘要历史变更失效修复_2026-10-26.md)
- [当前上下文治理](../docs/162_上下文治理与预算_2026-09-28.md)
- [207：Chat与长期记忆容量优化](../docs/207_Chat与长期记忆并发容量优化_2026-11-05.md)
- [208：长期记忆数据库召回与LangGraph迁移取舍](../docs/208_长期记忆数据库召回与LangGraph迁移取舍_2026-11-05.md)
