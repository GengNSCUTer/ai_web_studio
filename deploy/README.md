# 单机部署与运行检查

适用范围是 Linux 下的个人自部署工作台。PostgreSQL/pgvector 复用已有服务；本目录管理 API、Next.js、知识库/记忆/Durable 三个 Worker，必要时还可托管本地 Redis。运行代码与数据备份是两件事，备份恢复和版本化迁移在七项优化计划第二批处理。

## 安装

在仓库根目录执行，使用 Python 3.10+ 和 Node.js 22.18+（本轮验证 Node 24）：

```bash
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.txt -r deploy/requirements.txt -c deploy/constraints-py310.txt
backend/.venv/bin/python -m pip check
cd frontend
npm ci
npm run build
```

依据 `backend/.env.example` 创建 `backend/.env`，依据 `frontend/.env.local.example` 创建 `frontend/.env.local`。不要覆盖已有配置。数据库必须先存在并启用 pgvector；当前服务启动仍执行已有的结构检查，不宣称已经完成 Alembic 版本化迁移。

首次正式 HTTPS 部署必须配置：

- 后端 `APP_ENV=production`。
- `AUTH_SECRET_KEY` 和 `SECRET_ENCRYPTION_KEY` 使用两个独立的至少 32 字符随机值，不使用示例默认值。凭据加密密钥一旦用于存储数据，不能直接更换，否则旧凭据无法解密；已有开发数据需先规划密钥迁移。
- `CORS_ALLOWED_ORIGINS` 明确填写允许的前端来源，禁止 `*`。
- 前端 `AUTH_COOKIE_SECURE=true`；生产构建运行时默认也是 true。仅本机 HTTP 调试可显式设 false。
- `BACKEND_BASE_URL` 指向实际 API 内网地址。保持服务器凭据共享和私网 MCP 开关默认关闭。

## 启动与停止

已有同角色的 Screen 或其他托管实例时，先确认进程身份，再逐个停止旧实例，避免双 Worker 和端口冲突。

```bash
bash deploy/run_services.sh
```

`constraints-py310.txt` 固定本轮 Linux/Python 3.10 验证过的间接依赖。使用其他 Python 或平台需重新验证，而不是假定所有环境等价。

这个命令以前台形式运行 Supervisor；日常查看和控制：

```bash
bash deploy/run_services.sh ctl status
bash deploy/run_services.sh ctl restart backend
bash deploy/run_services.sh ctl stop memory-worker
bash deploy/run_services.sh ctl start memory-worker
backend/.venv/bin/python deploy/check_services.py
```

进程异常退出自动重启，启动连续失败则显示 FATAL，不假装已经正常。日志每个角色最多 5MB × 3 份，Unix 控制 socket 和日志目录限制为当前用户。正常停止时先发送终止信号；Worker 最长等待期限见 `supervisord.conf`，强制退出后仍依赖既有 Lease/幂等恢复，不能保证正在进行的第三方调用撤销。

默认不启动 Redis。若明确是全新单机部署、已有地址没有服务且没有待恢复的 Redis 数据，可设置 `AIWS_MANAGE_REDIS=true`；`AIWS_REDIS_BIN` 指向已安装的 redis-server，默认端口 36379，数据保存在 `.runtime/redis`，启用 AOF。后端 `KNOWLEDGE_REDIS_URL` 必须与之匹配。已有 Redis 部署应保持 false 并复用原地址和持久化目录，不要用空实例覆盖已有队列。

## 主机重启后的恢复

Screen 本身不负责主机重启恢复。正式运行可将 `aiws.service.example` 复制为 `~/.config/systemd/user/aiws.service`，修改其中仓库路径和 Node.js 所在 PATH，再执行：

```bash
systemctl --user daemon-reload
systemctl --user enable --now aiws.service
systemctl --user status aiws.service
```

不要同时再启动一套 Screen。需要退出登录后仍运行时，由管理员确认该用户的 linger 策略。外部 PostgreSQL/Redis 也需要各自的服务托管。此处提供的是单机进程恢复方案，不代表高可用或已配置外部告警。

如果当前终端的 HOME 与用户 systemd 管理器使用的 HOME 不同，复制文件后需先运行 `systemctl --user link /绝对路径/aiws.service` 再 reload/enable；不要为了修复路径问题修改 HOME。

## 健康状态怎么判断

- `GET /health`、`GET /health/live`：仅判断 API 进程可以响应；返回 200 不代表数据库和队列可用。
- `GET /health/ready`：并发执行 PostgreSQL `SELECT 1` 和 Redis `PING`，单项有网络/查询等待上限；全部正常返回 200，否则 503。结果不暴露内部地址、密码或异常正文。
- `GET /health/workers`：独立检查 Durable Worker 心跳，至少一个 `idle/running` 实例在 90 秒内报活返回 200，缺失、全部停止/过期或数据库查询失败返回 503；即使没有待执行任务也检查。只返回状态与检查耗时，不公开 Worker 标识、任务或连接信息。异常强杀可能在旧心跳超过 90 秒后才被识别，不能承诺瞬时发现。
- `deploy/check_services.py`：再加上 `/health/workers`、前端页面、Next.js `/api/health/ready` 到后端的真实转发链路，以及五个应用进程 RUNNING 状态；全部正常退出码 0，否则 1。可接外部定时监控，但当前未实现通知渠道。
- Web ready 与后台能力分开：Worker 故障不使仍可用的普通 Chat 被摘流。知识库/记忆 Worker 目前只覆盖受管进程状态；Durable 的陈旧 Worker、排队/租约告警继续查看运行面板。近期有心跳不证明工具凭据有效或端到端任务成功，探针不执行收费模型或外部工具请求。

建议就绪检查间隔 15—30 秒，不在每个业务请求前额外调用，也不调用收费模型 API 作为健康探针。

## 公网入口边界

本仓库默认只监听回环地址。正式对外服务需配置可信 HTTPS 反向代理、限制请求体大小，并关闭流式响应缓冲；TLS 证书、域名、防火墙和外部告警必须按实际部署验证。本轮本机验收不代表已经公开上线。
# 第二批数据与迁移操作

## 升级

先在停写窗口执行成套备份，然后使用显式迁移命令。应用启动只检查版本，不会自行 `ALTER TABLE`：

```bash
PYTHONPATH=backend backend/.venv/bin/python deploy/data_backup.py backup /path/to/backup --pg-bin /path/to/pg-bin
PYTHONPATH=backend backend/.venv/bin/python -m scripts.migrate_database upgrade --adopt-legacy --backup-dir /path/to/backup
```

非空历史库只有在备份清单对应当前数据库时才允许 `--adopt-legacy`。版本、表、列、索引不匹配会拒绝收编；迁移失败不会写入新版本。

## 恢复演练

恢复只能写入新建的隔离数据库和两个不存在的目录，不能覆盖正在运行的数据目录：

```bash
PYTHONPATH=backend backend/.venv/bin/python deploy/data_backup.py restore /path/to/backup \
  --pg-bin /path/to/pg-bin --target-db aiws_restore_test \
  --uploads /srv/aiws-restore/uploads --indexes /srv/aiws-restore/indexes
```

恢复后再对隔离库运行迁移并执行用户、知识库 Chunk、上传文件和索引文件查询。备份不包含 `.env` 或解密密钥，恢复服务必须注入原 `SECRET_ENCRYPTION_KEY`。
