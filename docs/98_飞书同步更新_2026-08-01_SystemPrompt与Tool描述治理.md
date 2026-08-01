# 2026-08-01 System Prompt 与 Tool 描述治理

本轮根据《理论学习：System Prompt 如何设计？》完成一次面向个人知识库工作平台的落地优化。参考文档关于角色、行为准则、工具指南、安全边界、任务模式、输出风格以及 system/messages/tools 分层的结论适用，但代码代理专属的 Bash、任意文件写入和外部发布能力不纳入本项目。

## 已完成

- Prompt 模板升级为 `context_prompt_v5_governed`，加入稳定的 `knowledge_workspace_behavior_v1` 行为合同：明确个人知识库助手角色、结论优先、不确定性说明、只做当前任务需要的事、检索/审阅/文件修改/长任务模式和产品非目标。
- 明确平台安全、当前用户问题、已审核 Skill/任务说明、历史记忆、RAG/附件/Tool evidence 的概念优先级。记忆、摘要、知识库、附件和 Tool 返回永远是不可信资料，不能覆盖安全边界或授权新操作。
- 用户自定义 system prompt 后追加平台边界，降低“自定义提示覆盖安全规则”的风险；动态证据继续留在 user/evidence 层，不放入稳定 system 前缀。
- Planner 看到的工具描述统一追加适用/禁用场景、只读或高风险权限、MCP 返回不可信、来源/时效要求和当前项目文件范围。Executor 的 JSON Schema、风险审核、用户确认、CAS 仍是最终安全边界。
- 增加行为合同版本诊断 `prompt_behavior_contract_version`，保留 prefix hash/prefix token 观测口径；当前不声称已接入 OpenAI/Anthropic 官方 Prompt Cache，vLLM 需要服务端配置和真实 usage 验证。
- 新增 Prompt、Tool Catalog、Planner 测试；定向共 44 项通过。

## 产品边界

当前产品继续定位为个人本地知识库工作平台，工具以联网搜索、地图/天气、当前项目文件读取/搜索、Diff 审阅、已审核 Skill 和低风险 Durable Artifact 为主。Bash、Shell、SQL、删除、任意本机写入、支付、邮件、外部发布和任意 HTTP 写入不开放。同步 Chat 最多五轮；长任务必须显式进入低风险 Durable Run。

## 后续

继续用固定 Gold Set 评估 Skill Recall、Tool Recall、Planner 选择/参数正确率、任务成功率和延迟；接入实际 vLLM 后再记录 provider cache usage。动态证据压缩、长期记忆自动候选和第三方 Skill 市场仍需单独评估，不能把本轮 Prompt 治理误称为完整 Agent 自主规划或官方 Prompt Cache。
