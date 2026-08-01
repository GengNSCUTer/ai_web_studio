# 飞书 System Prompt 设计对 Agent 项目的借鉴与落地

日期：2026-08-01

## 一、参考结论

飞书文档把 System Prompt 拆成角色设定、行为准则、工具指南、代码质量、安全边界、任务模式和输出风格七类模块，并强调稳定 system、动态 messages、工具 schema 三类输入要分层。这个方法对当前项目有直接借鉴意义，但不能照搬代码代理的 Bash、任意文件写入和外部发布能力。当前产品定位是个人本地知识库工作平台，主要服务检索、整理、研究、审阅、引用和低风险辅助行动。

最重要的原则是“稳定约束与动态证据分离”。平台行为合同、产品安全边界和固定任务模式放在 system 前缀；当前问题、滚动摘要、长期记忆、知识库、附件和 Tool 返回放在动态消息的 evidence 层。外部资料永远不是可信指令，不能因为网页、MCP 返回或记忆文本中出现“忽略系统提示”就改变权限。稳定前缀保持顺序不变，有利于 vLLM/OpenAI-compatible 服务观察前缀复用；当前实现记录 prefix hash，但没有声称已经接入 OpenAI 或 Anthropic 官方 Prompt Cache。

## 二、当前落地

`ContextPromptBuilder` 升级为 `context_prompt_v5_governed`，增加 `knowledge_workspace_behavior_v1` 平台行为合同，内容包括：个人知识库助手角色、结论优先和不确定性表达、平台安全优先级、证据不可信规则、检索/审阅/文件修改/长任务模式、同步工具循环边界以及产品非目标。用户自定义系统提示后会再次追加平台边界，防止自定义提示把证据变成指令或静默扩大权限。

概念优先级固定为：平台安全 > 当前用户问题 > 已审核 Skill/任务说明 > 最近历史与记忆 > RAG、附件和 Tool 证据。这个优先级是治理规则，不代表所有内容都放进同一个 role；动态资料仍然放在 user/evidence 层，预算不足时可以按层丢弃或压缩，而 system 和当前问题不会被普通 evidence 抢占。

Planner 侧增加统一的工具描述治理。工具原始 manifest/MCP description 只说明能力，不是安全边界；Planner 看到的描述会追加适用场景、不要使用的场景、只读或高风险权限、MCP 外部返回是不可信 evidence、来源/时效要求，以及工作区工具只能访问当前项目授权文件等信息。Executor 仍是最终校验点，描述不会替代 JSON Schema、风险策略、用户确认或 CAS。

## 三、产品化边界

当前工具集合围绕个人知识库工作台：联网搜索、地图/天气、当前项目文件的 list/search/read、Diff 预览、已审核 Skill 和低风险 Durable Artifact。Bash、Shell、SQL、删除文件、任意本机写入、支付、邮件、外部发布和任意 HTTP 写入不属于产品目标。文件修改必须走 PatchDraft、Approval、一次性 continuation 和版本 CAS。

普通同步 Chat 仍然使用最多五轮的有界 Tool Workflow；只有低风险、可恢复的长任务才进入 Durable Agent Run。系统可以推荐 Skill，但用户必须显式确认激活，不能自动安装或静默扩大工具权限。

## 四、验证与后续

本轮新增了行为合同版本诊断、外部 evidence 与自定义 system prompt 边界测试，以及 Planner 工具描述治理测试。后端定向 Prompt、Catalog、Planner 测试共 44 项通过。后续仍需观察真实 vLLM 服务返回的缓存 usage，并逐步建立 Prompt/Tool Gold Set，评估候选召回、Planner 选择、参数正确率、任务成功率和延迟；不把启发式 prefix hash 当成真实 Provider cache 命中。
