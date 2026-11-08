# 单机部署与运行检查

2026-11-08后续恢复升级：完成仓库外备份，执行20261108_02和20261108_03再重启唯一Chat Worker。普通工具图复用Agent业务表和原生PG池，崩溃后最多三次领取、两次最终生成，原任务与工具时限不重置；写入结果不明不自动重发。说明与故障验收见[217](../docs/217_Chat与工具统一持久化恢复_2026-11-08.md)。

适用范围是 Linux 下的个人自部署工作台。PostgreSQL/pgvector 复用已有服务；本目录管理 API、Next.js、知识库/记忆/Durable/Chat 四个 Worker，必要时还可托管本地 Redis。运行代码与数据备份是两件事，备份恢复和版本化迁移在七项优化计划第二批处理。

2026-11-08新增普通Chat独立进程：先执行20261108_01迁移，再用`bash deploy/run_services.sh ctl reread`和`ctl update`加入唯一`chat-worker`。Web只创建Run和订阅事件，关闭页面不停止生成；POST取消才撤回执行权。`/health/workers`分别检查Chat与Durable心跳，Web依赖就绪不表示生成Worker在线。默认用户2个未完成任务、全局16个运行任务、每Worker4并发、排队300秒/执行900秒、输出8192Token、20万字符与8MiB事件；详见环境样例和[216说明](../docs/216_Chat独立任务与统一取消_2026-11-08.md)。备份暂停写入者时必须包括Chat Worker，不启动同职责的旧Screen实例。

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

依据 `backend/.env.example` 创建 `backend/.env`，依据 `frontend/.env.local.example` 创建 `frontend/.env.local`。不要覆盖已有配置。数据库必须先存在并启用 pgvector；使用下方显式Alembic迁移流程，服务启动只校验版本，不自动修改表。

## LangGraph迁移部署（2026-11-06）

同步工具、Chat、Durable、文件审批、文档、记忆提取与知识索引已接入图编排，说明见[迁移与验收](../docs/215_LangGraph迁移完成说明与最终验收_2026-11-06.md)。安装继续使用上面的requirements与Python3.10约束；不能用尚未安装新依赖的旧conda环境启动服务。

- 数据库`20261106_01`新增独立`aiws_graph`原生检查点schema；`20261106_02`按`(step_id,runtime_key)`区分同一步骤的审批图与执行图。运行下面已有的备份/迁移命令，不在Web请求内调用Saver.setup。
- `TOOL_ORCHESTRATION_ENGINE`已退役，遗留环境值不生效。同步链只有图入口，失败后不自动换引擎重做。
- 退役前正式库没有旧AgentRun；缺失/未知`runtime_engine`的导入或旧备份任务会拒绝执行/恢复，需用户重新发起。若部署其他已有旧任务的实例，先清点并安排旧版本处理或让用户重新提交，不能仅改JSON版本号绕过核验。
- 普通Chat没有图持久化；Durable工具Step、文件和文档审批、文档执行使用原生PG检查点。记忆/知识Job仍按原Job租约、游标、generation及Outbox恢复，不宣传token续接。
- Web与Durable Worker各自管理有界原生检查点池（最多4连接）；审批池也是独立有界4连接。容量规划要计入这些连接以及SQLAlchemy池，不能只按Web池估算。用户等待审批期间不持有连接。
- PDF/中文资料导入脚本也改为投递正式解析/索引Job。必须先启动独立knowledge-worker与Redis；等待超时不退回脚本内同步重建索引，任务仍可在Job页追踪。
- 回退应用时保留新迁移链和数据库schema。已产生双角色线程的数据库不能直接恢复旧`step_id`唯一约束；若必须回退数据，应停全部写入者并按备份恢复流程执行，明确备份后的新增数据处理。
- 现有Supervisor仍各保留一个角色；API32007、前端32008、PG35433、Redis36379不变。沙箱仍因Rootless前提不足保持禁用，不向Web授予Docker权限。

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
