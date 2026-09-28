# 智能知识 Agent 平台

面向个人与小团队的本地知识工作台。后端基于 FastAPI、SQLAlchemy、PostgreSQL/pgvector 与 Redis，支持文档解析和混合检索、流式多轮对话、长期记忆、受控 Tool/MCP 工作流，以及用户确认后的低风险只读 Durable 任务。

当前功能和边界从 [文档导航](docs/README.md) 与 [项目全景](docs/159_项目当前全景与第二轮优化收口_2026-09-28.md) 查看；旧阶段记录仅代表当时状态。

## 核心链路

- RAG：文档解析 -> Chunk -> Embedding -> pgvector/BM25 -> RRF -> Rerank -> 引用上下文
- Chat：最近消息、滚动摘要、长期记忆、附件、RAG 和 Tool 结果统一进入 Token 预算
- Tool：代码筛选候选和执行安全校验，LLM Planner 只在候选集内生成结构化调用计划
- Knowledge Worker：PostgreSQL Outbox -> Redis Stream -> Worker -> Lease/Heartbeat -> inactive generation -> CAS 激活
- Durable Tool：Tool Plan -> 用户预览/确认 -> Run/Step/Checkpoint -> Worker -> 原会话终态回写；任务页支持状态观察、受控重放和脱敏 JSONL 审计

## 模型服务

- `openai-compatible`：兼容标准 `/v1/models` 与 `/v1/chat/completions` 的在线服务
- `ollama`：使用 Ollama 原生 `/api/tags`、`/api/chat` 和 `/api/embed`
- `vllm`：显式本地 Provider，复用 vLLM 的 OpenAI-compatible `/v1` 服务；Base URL、模型名和可选 API Key 均由用户配置

## 本地运行

1. 复制 `backend/.env.example` 和 `frontend/.env.local.example` 为对应本地配置文件。
2. 启动 PostgreSQL + pgvector 与 Redis。
3. 执行 `backend/scripts/run_dev.sh` 启动 API。
4. 执行 `backend/scripts/run_knowledge_worker.sh` 启动知识库 Worker。
5. 若在设置中启用“自动生成待审核记忆候选”，执行 `backend/scripts/run_memory_candidate_worker.sh` 启动 Memory Worker。
6. 若要执行用户确认的 Durable 只读任务，执行 `backend/scripts/run_agent_tool_worker.sh` 启动 Agent Worker。
7. 在 `frontend` 下执行 `npm run dev -- --hostname 127.0.0.1 --port 32008`。

后端默认地址为 `http://127.0.0.1:32007`，接口文档位于 `/docs`。

## 验证

```bash
cd backend
PYTHONPATH=. python -m unittest discover -s tests -v

cd ../frontend
npm run lint
npm run build
```

## 工程边界

当前同步 Agent 按运行模式受轮次、总调用数、并发和时长预算约束，不宣称完全自主 Agent。后台知识库与只读 Tool 任务采用至少一次投递、幂等处理、有限重试/DLQ、Lease/Heartbeat 与 CAS/Fencing 的可恢复最终一致性；不是跨 PostgreSQL、Redis 和第三方服务的强事务，也不是 Exactly Once。Planner 的后台建议不会自动创建 Durable Run。
