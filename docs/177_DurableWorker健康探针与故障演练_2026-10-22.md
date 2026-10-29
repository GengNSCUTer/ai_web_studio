# Durable Worker 健康探针与故障演练

2026-10-22，Day 20 学习时发现：Web 依赖就绪不代表后台执行可用。现有运维检查已覆盖 Supervisor 进程状态，但未消费 Durable 数据库心跳，进程存在、循环卡住时仍可能显示正常。

## 实现范围

保留 `/health/live` 的 Web 存活语义，以及 `/health/ready` 的 PostgreSQL/Redis 依赖就绪语义。新增 `GET /health/workers`，查询已有 `agent_worker_heartbeats`：至少一个 `idle/running` 实例在最近 90 秒报活返回 200，否则返回 503。空队列也检查，数据库不可达或查询超时也不能显示后台正常。

入口只公开 `status`、`durable_worker.status` 和耗时，不公开用户任务、Worker 名称、凭据或异常正文；不触发工具调用或模型生成，也不写心跳。复用独立、连接超时 2 秒/语句超时 2 秒的 PostgreSQL 探针连接，在线程池运行，外层等待上限 3 秒；不借用业务连接池。

运维巡检新增消费 `/health/workers`。即使 Supervisor 显示 RUNNING，心跳失效也会使巡检返回 `ok=false`、退出码 1。Web 仍可接普通 Chat，不能因后台故障一并摘流。

## 文件职责

| 文件 | 职责 |
| --- | --- |
| `backend/app/services/health_service.py` | 复用受限时 PostgreSQL 连接，查询 Worker 存活心跳，转换为后台能力状态 |
| `backend/app/api/routes/health.py` | `/health/workers` 返回 200/503，并禁止缓存 |
| `deploy/check_services.py` | 将 Worker 心跳与原有进程/页面/依赖检查一起计入总体结论 |
| `backend/tests/test_health.py` | API 状态、错误脱敏、超时及真实 PostgreSQL 心跳 SQL 场景 |
| `backend/tests/test_service_inspection.py` | 验证进程 RUNNING 时仍识别心跳探针 503 |

没有改表、扩大工具权限或修改现有租约。知识库/记忆 Worker 仍沿用进程检查；它们尚未新增同等心跳探针。

## 验证结果

47 项健康、Worker Registry 与 Durable 定向测试全部通过（5.126 秒），没有 RuntimeWarning。真实 PostgreSQL 集成用连接内临时表遮蔽同名表，覆盖空记录、近期 idle/running、近期 stopped、过期 idle/running，以及多实例部分可用；没有修改真实 Worker 记录。API 另覆盖数据库异常脱敏和等待超时。compileall 与 git diff --check 通过。

真实服务演练只操作当前 Supervisor 的 Durable Worker，使用 finally 保证启动恢复，没有启动第二套服务：

| 状态 | `/health/live` | `/health/ready` | `/health/workers` |
| --- | --- | --- | --- |
| 正常运行 | 200 | 200 | 200 |
| 主动停止 Durable Worker 后 | 200 | 200 | 503 |
| 重新启动并注册心跳后 | 200 | 200 | 200 |

恢复后 `deploy/check_services.py` 返回 `ok=true`，五个应用进程均为 RUNNING，前端及 BFF 转发正常。此次没有触发付费模型请求；没有修改前端，不要求重新构建或浏览器交互验收。

## 能证明什么

探针证明近期至少一个 Durable Worker 能向数据库报活；停止后能发现后台能力不可用。强杀未写 stopped 时，最多要等心跳超过现有 90 秒有效期，再由下一次巡检发现。没有伪造新鲜心跳来测试过期，过期分支由真实数据库临时表覆盖。

它不能证明外部 API Key 有效、某个工具业务成功、所有步骤都未卡住，也不等于完整合成业务巡检。现有任务状态、队列逾期、租约告警和 Trace 仍需一起观察。外部通知渠道没有接入。
