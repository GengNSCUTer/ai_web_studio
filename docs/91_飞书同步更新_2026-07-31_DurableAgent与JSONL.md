# 2026-07-31：Durable Agent、Artifact、Skill 与 JSONL 导出

本轮围绕 Durable Agent Runtime、Tool Artifact、Skill 基础、文件版本、安全工具边界和生产观测完成一批增量。

## 已完成

- 新增低风险只读 Tool DAG 控制面：Run、Step、PostgreSQL Outbox 和 Checkpoint 同事务创建，HTTP 返回后由独立 Worker 执行。
- Worker 支持 DAG 依赖、Result Binding、Lease、owner + lease version fencing、有限重试、退避与数据库 DLQ。
- 显式幂等键绑定完整请求摘要；同键不同请求拒绝复用。
- Run 关联的项目、会话和 Assistant Message 必须属于当前用户且相互一致。
- Tool 完整结构化结果保存为 AgentArtifact，Prompt 不默认注入全文；新增 ACL 范围内的 Artifact list/read 工具。
- 新增内置 declarative Skill manifest、用户安装状态、依赖 Tool 检查和设置页开关。
- 新增文件 revision 历史和安全恢复提案；恢复仍然生成 Diff 与 Approval，通过 CAS 后才创建新 revision。
- 新增 ChatRuntimeMetric 和 Agent 汇总接口，统计 Chat Token/Cache、RAG 召回与注入、Tool/Run/Step/Outbox 状态。
- 会话导出增加 JSONL：一行一个 `aiws.conversation.v1` 事件，带稳定 Event ID，并过滤服务器本地存储路径。

## JSONL 架构判断

项目不会将 PostgreSQL 会话主存储替换为 JSONL。云端多用户服务仍需要关系数据库提供事务、ACL、索引、关联查询和 CAS。JSONL 只作为可移植导出、冷存档、调试回放和离线评测格式，借鉴 append-only event log 的优点。

## 安全边界

- Durable 通用 Worker 当前只接受 `low-risk + read-only` Tool。
- 文件写入继续使用专用 `PatchDraft -> Approval -> CAS -> FileRevision`。
- Skill 只是无可执行代码的声明式编排，不是第三方 Plugin Marketplace。
- Shell、SQL、删除文件、支付、邮件发送、外部发布和任意 HTTP 写入仍未开放。
- 当前不宣称 Exactly-once，也不宣称任意写 Tool 已具备崩溃恢复和补偿。

## 验证

- 后端完整测试共运行 265 项，整体 `OK`，其中 3 项按外部集成条件跳过。
- Python compileall、前端 ESLint 与 Next production build 通过。
- PostgreSQL 新表和新增列已实际创建并核对。
- Playwright 桌面与 390px 移动视口通过，Skill 和 JSONL 真实接口可用，浏览器 console 无错误。

完整设计与边界记录见本地：`docs/90_DurableAgent运行时与Artifact_Skill演进_2026-07-31.md`。
