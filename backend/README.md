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
- 文件修改 Agent Run/Step/Checkpoint、Diff 审批与 FileRevision CAS
- 文本流聊天接口
- 文件上传落盘

## 当前主聊天链路

当前主链路是：

- `POST /api/chat/text-stream`

前端通过 Next BFF `/api/chat` 代理到这个接口，并用纯文本流方式消费。

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
- `POST /api/chat/text-stream`
- `GET /api/memories?status=pending`
- `GET /api/memories/extraction-jobs`
- `GET /api/agent-runtime/runs/{run_id}`
- `POST /api/agent-runtime/approvals/{approval_id}/challenge`
- `POST /api/agent-runtime/approvals/{approval_id}/apply`
- `GET /api/tools/settings?project_id={project_id}`
- `PATCH /api/tools/workspace-policies/{project_id}`
- `POST /api/uploads`

vLLM Prompt Cache 需要服务端启用 APC；客户端会发送隔离用 `cache_salt` 和流式 usage 请求：

```bash
vllm serve <model> --enable-prefix-caching --enable-prompt-tokens-details
```

工具权限默认是 `ask`。`full_workspace` 只允许当前项目内经过 ACL、Diff、版本 CAS
和 Revision 审计的文件修改自动应用，不代表宿主机、Shell、SQL 或外部写接口权限。

## 近期待做

- 会话搜索
- 上下文统计与可视化
- 摘要压缩升级
- 聊天接口进一步整理
