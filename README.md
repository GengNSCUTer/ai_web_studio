# 智能知识 Agent 平台

面向个人与小团队的本地知识工作台。后端基于 FastAPI、SQLAlchemy、PostgreSQL/pgvector 与 Redis，支持文档解析和混合检索、流式多轮对话、长期记忆、受控 Tool/MCP 工作流，以及用户确认后的低风险只读 Durable 任务。

当前功能和边界从 [文档导航](docs/README.md) 与 [项目全景](docs/159_项目当前全景与第二轮优化收口_2026-09-28.md) 查看；旧阶段记录仅代表当时状态。

## 核心链路

- RAG：文档解析 -> Chunk -> Embedding -> pgvector/BM25 -> RRF -> Rerank -> 引用上下文
- Chat：API 创建 ChatRun，独立 Chat Worker 组装上下文并流式生成。浏览器通过原生 SSE 订阅，关闭页面后任务可以继续运行
- Tool：代码筛选候选和执行安全校验，LLM Planner 只在候选集内生成结构化调用计划
- Knowledge Worker：PostgreSQL Outbox -> Redis Stream -> Worker -> Lease/Heartbeat -> inactive generation -> CAS 激活
- Durable Tool：Tool Plan -> 用户预览/确认 -> Run/Step/Checkpoint -> Worker -> 原会话终态回写；任务页支持状态观察、受控重放和脱敏 JSONL 审计

## 模型服务

- `openai-compatible`：兼容标准 `/v1/models` 与 `/v1/chat/completions` 的在线服务
- `ollama`：使用 Ollama 原生 `/api/tags`、`/api/chat` 和 `/api/embed`
- `vllm`：显式本地 Provider，复用 vLLM 的 OpenAI-compatible `/v1` 服务；Base URL、模型名和可选 API Key 均由用户配置

## 本地运行

首次安装、生产配置和健康检查见 [部署说明](deploy/README.md)。日常使用 Supervisor 统一托管，默认使用 `backend/.venv`。

1. 复制 `backend/.env.example` 和 `frontend/.env.local.example` 为对应本地配置文件。
2. 确认 PostgreSQL + pgvector 和 Redis 可用。新库先迁移。已有库先按 [升级流程](deploy/README.md#升级) 停写并备份。
3. 在仓库根目录执行下面的命令。Supervisor 启动 API、前端与 Chat、知识索引、记忆、摘要、Durable Worker。沙箱 Worker 需要单独满足部署条件。

```bash
PYTHONPATH=backend backend/.venv/bin/python -m scripts.migrate_database upgrade
bash deploy/run_services.sh
```

在另一个终端用 `bash deploy/run_services.sh ctl status` 查看进程，或运行 `backend/.venv/bin/python deploy/check_services.py` 检查健康状态。前端默认地址是 `http://127.0.0.1:32008`，后端是 `http://127.0.0.1:32007`，接口文档位于 `/docs`。

仅在开发调试时可以单独启动角色。API 用 `bash backend/scripts/run_dev.sh`，Chat 生成用 `AIWS_DB_ROLE=chat-worker PYTHONPATH=backend backend/.venv/bin/python -m scripts.run_chat_worker`，前端在 `frontend` 下运行 `npm run dev -- --hostname 127.0.0.1 --port 32008`。同一角色只启动一套。连接池容量见 [数据库连接预算](deploy/README.md#数据库连接预算)。

## 验证

```bash
PYTHONPATH=backend backend/.venv/bin/python -m unittest discover -s backend/tests -v

cd frontend
npm run lint
npm run build
```

## 工程边界

当前同步 Agent 按运行模式受轮次、总调用数、并发和时长预算约束，不宣称完全自主 Agent。后台知识库与只读 Tool 任务采用至少一次投递、幂等处理、有限重试/DLQ、Lease/Heartbeat 与 CAS/Fencing 的可恢复最终一致性；不是跨 PostgreSQL、Redis 和第三方服务的强事务，也不是 Exactly Once。Planner 的后台建议不会自动创建 Durable Run。
