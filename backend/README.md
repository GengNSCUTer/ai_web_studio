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
- 在线 OpenAI-compatible、Ollama 与 vLLM 模型调用
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
- `POST /api/uploads`

## 近期待做

- 会话搜索
- 上下文统计与可视化
- 摘要压缩升级
- 聊天接口进一步整理
