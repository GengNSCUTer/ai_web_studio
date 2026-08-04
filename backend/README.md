# Backend

这个目录是当前智能问答网页的 FastAPI 后端。

技术栈：

- FastAPI
- SQLAlchemy
- Pydantic
- PostgreSQL
- Uvicorn

## 当前已完成

- 用户注册 / 登录
- JWT 鉴权
- 用户级会话隔离
- 会话 CRUD
- 消息持久化
- 用户设置持久化
- Provider 模型列表查询
- Provider 测试连接
- 在线 OpenAI-compatible、Anthropic、Ollama 与 vLLM 模型调用
- OpenAI/Anthropic Prompt Cache 参数与真实 cached-token usage 观测
- 长期记忆 pending/active 审核生命周期与持久化候选 Worker
- 长期记忆 TTL、冲突版本 supersede、显式 revoke 与 Query-aware 召回
- RAG 固定 Gold Set 的 Vector/BM25/Hybrid/Rerank 对照评测与降级诊断
- Tool ResultQualityContract 的业务质量门禁：语义空结果、stale、依赖阻断
- 文件修改 Agent Run/Step/Checkpoint、Diff 审批与 FileRevision CAS
- 低风险只读 Tool DAG 的 PostgreSQL Outbox、Lease/Fencing、有限重试、DLQ 与 Artifact
- 内置声明式 Skill manifest、用户启用状态与依赖能力检查
- 会话 Markdown / JSON / JSONL 导出
- 文本流聊天接口
- 文件上传落盘

## 当前主聊天链路

当前主链路是：

- `POST /api/chat/events-stream`

前端通过 Next BFF `/api/chat` 代理到这个接口，并用 NDJSON 事件流消费。
`POST /api/chat/text-stream` 仅保留为旧的纯文本兼容入口，不包含工具事件和 reasoning 增量。

旧的 SSE 方案已经不再作为主链路使用。

## 环境

推荐 Python 环境：

- `/disk2/gengnan/conda_envs/ai_web_studio`

PostgreSQL：

- 主机：`127.0.0.1`
- 端口：`35433`
- 数据库：`ai_web_studio`

配置文件：

- [`.env`](/disk2/gengnan/ai_web_studio/backend/.env)

## 启动

```bash
cd /disk2/gengnan/ai_web_studio/backend
source /disk2/gengnan/miniconda3/etc/profile.d/conda.sh
conda activate ai_web_studio
./scripts/run_dev.sh
```

需要后台任务时另开终端：

```bash
./scripts/run_knowledge_worker.sh
./scripts/run_memory_candidate_worker.sh
./scripts/run_agent_tool_worker.sh
```

## 主要接口

- `GET /health`
- `POST /api/auth/register`
- `POST /api/auth/login`
- `GET /api/auth/me`
- `GET /api/models`
- `GET /api/settings`
- `PATCH /api/settings`
- `POST /api/settings/test-provider`
- `GET /api/conversations`
- `POST /api/conversations`
- `PATCH /api/conversations/{conversation_id}`
- `DELETE /api/conversations/{conversation_id}`
- `GET /api/conversations/{conversation_id}/messages`
- `POST /api/chat/events-stream`
- `POST /api/chat/text-stream`（旧的纯文本兼容入口）
- `GET /api/memories?status=pending`
- `POST /api/memories/{memory_id}/revoke`
- `GET /api/memories/extraction-jobs`
- `GET /api/agent-runtime/runs/{run_id}`
- `POST /api/agent-runtime/tool-runs`
- `GET /api/agent-runtime/metrics`
- `GET /api/agent-runtime/files/{file_id}/revisions`
- `POST /api/agent-runtime/approvals/{approval_id}/challenge`
- `POST /api/agent-runtime/approvals/{approval_id}/apply`
- `GET /api/tools/settings?project_id={project_id}`
- `GET /api/tools/skills`
- `PUT /api/tools/skills/{skill_key}`
- `PATCH /api/tools/workspace-policies/{project_id}`
- `POST /api/uploads`
- `POST /api/knowledge-bases/{knowledge_base_id}/eval-sets/{eval_set_id}/matrix-runs`

RAG Gold Set 评测工具：

```bash
cd /disk2/gengnan/ai_web_studio/backend
PYTHONPATH=. python scripts/import_rag_gold_set.py \
  --knowledge-base-id <knowledge-base-id> --user-id <user-id>
PYTHONPATH=. python scripts/run_rag_eval_matrix.py \
  --knowledge-base-id <knowledge-base-id> --eval-set-id <eval-set-id> \
  --user-id <user-id> --output /tmp/rag-matrix.json
```

`evals/rag_gold_set.json` 只提供稳定的案例问题和标注格式。每个 Case 必须先由
人工绑定真实文档/Chunk，导入脚本会拒绝缺少目标、文件名不唯一或 Chunk 标记不唯一的
样本；因此未运行脚本前不应把任何命中率写入简历。

vLLM Prompt Cache 需要服务端启用 APC；客户端会发送隔离用 `cache_salt` 和流式 usage 请求：

```bash
vllm serve <model> --enable-prefix-caching --enable-prompt-tokens-details
```

PostgreSQL 集成测试：

```bash
cd /disk2/gengnan/ai_web_studio/backend
TEST_POSTGRES_URL=postgresql+psycopg://user:password@127.0.0.1:35433/postgres \
  /disk2/gengnan/conda_envs/ai_web_studio/bin/python -m unittest discover -s tests -p 'test_*integration.py'
```

`TEST_POSTGRES_URL` 必须指向具备创建/删除临时数据库权限的 PostgreSQL/pgvector
实例。该套测试会真实验证 `FOR UPDATE SKIP LOCKED`、Lease 接管、版本 Fencing
和幂等键竞争；未配置时只会明确跳过，不将 SQLite 结果作为 PostgreSQL 并发结论。

工具权限默认是 `ask`。`full_workspace` 只允许当前项目内经过 ACL、Diff、版本 CAS
和 Revision 审计的文件修改自动应用，不代表宿主机、Shell、SQL 或外部写接口权限。

Durable Tool Worker 当前只接受 `low-risk + read-only` 工具。完整 Tool Result 保存为
`AgentArtifact` 并按需读取，不默认全部注入 Prompt。声明式 Skill 只能编排现有已审核能力，
不等于第三方插件市场，也不能带来额外权限。

会话在线数据仍以 PostgreSQL 为真相源。`format=jsonl` 仅用于可移植导出、冷存档、
调试回放和离线评测，不替代多用户事务存储。

## 近期待做

- 会话搜索
- 上下文统计与可视化
- 摘要压缩升级
- 聊天接口进一步整理
