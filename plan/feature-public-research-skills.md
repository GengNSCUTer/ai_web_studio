# 公共资料 MCP 与文档 Skill 接入（2026-10-11）

用户授权：调研适合个人/小团队知识库工作台的主流 MCP/Skill，完成接入和测试。继续 Day 7 学习记录，不以功能开发代替计算机基础反馈。

## 已核实的选型

- Exa 官方 Streamable HTTP MCP：https://mcp.exa.ai/mcp；实际 tools/list 为 web_search_exa、web_fetch_exa。补搜索后读取页面正文的能力，无 Key 模式限流，不承诺持续免费或生产 SLA。
- Context7 官方 Streamable HTTP MCP：https://mcp.context7.com/mcp；实际 tools/list 为 resolve-library-id、query-docs。补库名解析与技术资料查证，无 Key 模式限流。
- Anthropic doc-coauthoring：https://github.com/anthropics/skills/blob/main/skills/doc-coauthoring/SKILL.md。借鉴收集背景、分节起草和读者问题检查；转成平台审核的声明式 Skill，不执行上游 Shell/文件操作，也不假装已实现独立读者 Agent。

## 实施与验收

1. 增加四个固定只读 MCP Tool，使用已有 MCP Client、Schema、Executor、质量门和证据投影；限制请求数量/文本长度。公共查询不能带明显凭据，网页 URL 不允许私网、回环、带认证信息和非 HTTP(S) 协议。
2. 使用可复用的文本结果解析器，规范化 Exa 网页和 Context7 文档/库 ID，保留来源 URL 和足够正文；格式漂移/空响应/错误文本失败关闭，不把错误包装成有效证据。
3. 增加来源研究简报、技术文档查证、文档协作起草三个 Skill，用户显式选择；协作起草仅用项目文件读取与审批提案，不自动修改正文。冻结计划的后台执行不在这次扩展范围。
4. 定向测试覆盖解析、业务质量、参数/隐私限制、候选召回、Skill 范围/版本锁和原有工具回归。全量后端测试用实际 PostgreSQL/pgvector，compileall，前端检查；真实 MCP 调用与 V3.2 Planner → Tool → 最终回答 → 数据库/浏览器验证，清理临时项目。
5. 记录实际成功和失败场景、服务限流与功能边界，更新本地文档和飞书。GitHub 提交/推送按既有用户授权及协作规则执行。

## 边界

官方远端服务也属于外部证据源。只读工具不能改变系统权限，查询不主动携带工作区全文或用户长期记忆。自动检测只能识别部分明显凭据，不能保证识别所有语义敏感信息；用户仍须选择可公开的查询。远端抓取不等于本地可任意联网，也不承诺绕过网站访问限制。

Day 7 基础部分仍需后续巩固与周测总结；不得提前标记完成或进入 Day 8。

## 完成记录

2026-10-11 实施 1—5 已完成。四个生产 Executor 调用 quality=valid，固定账号已实际安装三项新 Skill；V3.2 浏览器链路检查质量事件、公开回答引用、消息 done 和文件不变。后续复核修复正文预算、相邻标题边界和最终查证指令；技术文档 Skill 显式升级 1.0.1，SQLAlchemy/FastAPI 固定事实通过真实模型回归，早期答案错误和 Planner 超时保留记录。公开只读 Planner 去掉无关规则，保留 25 秒和总时限；新增固定降级分类。可选 Exa/Context7 Key 复用账号加密凭据，匿名限额与显式关闭分别处理。全量 671 项含实际 PostgreSQL/pgvector 通过，compileall/前端 lint/diff 检查通过。原文充分性增加 required_tools 通用策略，不自动猜参数；普通任务保留一个搜索兜底。临时项目清理，后端和 Worker 最新各一套。限制和文件职责见 docs/171，学习反馈见 Day 7 第十四节，开发不替代学习。本轮按用户明确授权提交推送。
