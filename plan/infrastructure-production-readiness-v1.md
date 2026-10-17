---
goal: 将个人知识库 Agent 工作台分三批完善为可部署、可恢复、可持续使用的应用
version: 1.0
date_created: 2026-10-17
last_updated: 2026-10-17
owner: AI Web Studio
status: 'In progress'
tags: [infrastructure, deployment, reliability, security]
---

# Introduction

![Status: In progress](https://img.shields.io/badge/status-In%20progress-yellow)

本计划承接第二轮面试复习后的代码审查。项目已具备完整的 Chat、RAG、记忆和工具执行链；本轮重点补齐交付和使用保障。七项问题分三批处理，每批单独实现、测试、记录，不以增加工具数量作为完成标准。

## 1. Requirements & Constraints

- **REQ-001**: 可重复安装。补齐直接依赖，移除启动脚本中的个人 Python 绝对路径，提供前后端与三个 Worker 的统一运行入口。
- **REQ-002**: 真实可用性。区分进程存活与依赖就绪，检查 PostgreSQL、Redis 和受管进程；异常退出自动拉起。
- **REQ-003**: 资源保护。第一批移出上传解析的事件循环阻塞；第二批再实施用户级并发、任务和存储限额。
- **REQ-004**: 生产配置。正式 HTTPS 部署使用 Secure Cookie；启动时检查独立 JWT/凭据加密密钥和明确的 CORS 来源。
- **REQ-005**: 数据恢复。第二批补版本化迁移、文件与数据库备份和真实恢复演练。
- **REQ-006**: Durable 使用价值。第三批优化同步调用后再后台重跑的入口，明确当前恢复对象是工具步骤，不是完整模型思考循环。
- **REQ-007**: 使用证据。第三批验证三条完整业务任务、小规模并发和用户试用，记录实际数据，不把单测数量或候选召回率当作产品成功率。
- **SEC-001**: 不提交密钥，不启动本地模型，不扩大 Shell、SQL、任意路径写入等工具权限。
- **CON-001**: 保留现有 PostgreSQL 服务、端口、数据和用户配置；只替换经核实属于本项目的旧服务实例。
- **CON-002**: 当前定位为个人自部署、账号数据隔离的工作台；没有团队成员和团队 RBAC 时，不宣称企业协作产品。

## 2. Implementation Steps

### Implementation Phase 1

- **GOAL-001**: 完成第一批“可交付”：干净环境安装通过，上传解析不占用事件循环，配置和服务健康状态可验证。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-001 | 在 backend/requirements.txt 声明 bcrypt、cryptography；deploy/requirements.txt 固定 Supervisor；backend/scripts/run_dev.sh 支持环境选择 Python。 | 是 | 2026-10-17 |
| TASK-002 | backend/app/api/routes/uploads.py 将整批落盘、解析和失败清理交给有独立并发限制的线程；backend/tests/test_upload_limits.py 验证并发与回滚。 | 是 | 2026-10-17 |
| TASK-003 | backend/app/core/config.py 启动检查生产密钥/CORS；frontend/src/lib/auth-cookie.ts 统一登录、注册、退出 Cookie 策略。 | 是 | 2026-10-17 |
| TASK-004 | backend/app/api/routes/health.py 保留 /health 存活检查并新增 /health/ready；backend/app/services/health_service.py 有限等待检查 PostgreSQL 和 Redis，不返回内部地址或异常原文。 | 是 | 2026-10-17 |
| TASK-005 | deploy/supervisord.conf 与 deploy/run_services.sh 管理前端、后端、知识库/记忆/Durable Worker；deploy/check_services.py 检查依赖和进程，验证退出后自动重启。 | 是 | 2026-10-17 |
| TASK-006 | 干净虚拟环境安装、相关/全量后端测试、前端 lint/build、真实浏览器在线模型测试；docs/173 记录结果并同步飞书。依赖 TASK-001 至 TASK-005。 | 是 | 2026-10-17 |

### Implementation Phase 2

- **GOAL-002**: 第二批“可保护、可恢复”，第一批验收后另行授权实施。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-007 | 基于 chat/uploads/knowledge 入口增加用户级并发、待处理任务和存储限额，验证拒绝不会留下半成品业务记录。 | 是 | 2026-10-17 |
| TASK-008 | 将 core/startup.py 的结构演进整理为可版本化迁移；先备份，再验证升级及失败处理，不直接删除旧数据。 | 是 | 2026-10-17 |
| TASK-009 | 增加 PostgreSQL、uploads 和 BM25 索引的备份/恢复脚本；在独立目标库恢复并验证三条核心查询。 | 是 | 2026-10-17 |
| TASK-010 | 将确定性后端、前端构建和受控集成回归接入自动化检查；外部收费 API 测试单独手动触发。 | 是 | 2026-10-17 |

### Implementation Phase 3

- **GOAL-003**: 第三批“可持续使用”，完成真实用户任务和运行证据。

| Task | Description | Completed | Date |
|------|-------------|-----------|------|
| TASK-011 | 审查 durable_handoff_service.py 与 Tool Trace 入口，提供执行前后台建议或安全复用已完成只读产物，避免默认重复执行；仍保留用户确认。 | 是 | 2026-10-17 |
| TASK-012 | 固定三个业务验收：带来源研究回答、Diff/Approval 文件修改、后台审阅重启后回传结果；覆盖部分失败和撤销。 | 是 | 2026-10-17 |
| TASK-013 | 分别进行 5/10/20 并发探测并记录完成率、首事件延迟、总时长、错误和依赖压力；不预先承诺容量。 | 是 | 2026-10-17 |
| TASK-014 | 邀请少量真实用户完成任务，记录问题和修复；用结果更新产品定位、部署说明和简历，停止无需求扩展工具。 | 否 | |

## 3. Alternatives

- **ALT-001**: 全量容器化和 Kubernetes 暂不采用；现有共享服务器与 PostgreSQL 可先用用户权限 Supervisor 接管，降低迁移风险。
- **ALT-002**: 不把同步解析改成完整异步库迁移；第一批隔离阻塞代码并限制并发，保留现有文件格式行为。

## 4. Dependencies

- **DEP-001**: Python 3.10+、Node.js 22.18+（支持直接运行本仓库的 TypeScript 测试）、npm lockfile、现有 PostgreSQL/pgvector 与 Redis。
- **DEP-002**: 在线 OpenAI-compatible 模型用于端到端验收，默认 DeepSeek-V3.2；凭据仅从现有用户配置读取。

## 5. Files

- **FILE-001**: backend/requirements.txt、backend/scripts/run_dev.sh、deploy/：安装与托管。
- **FILE-002**: backend/app/api/routes/uploads.py、health.py、backend/app/services/health_service.py、backend/app/core/config.py：第一批后端变更。
- **FILE-003**: frontend/src/lib/auth-cookie.ts、frontend/src/app/api/session/：认证 Cookie。
- **FILE-004**: docs/173_上线准备七项优化计划与第一批验收_2026-10-17.md、docs/07_当前实现进展与下一步计划.md、docs/README.md：结果与导航。
- **FILE-005**: backend/app/services/resource_quota_service.py、backend/app/api/resource_limits.py、backend/app/models/resource_lease.py：用户级并发、存储预留和待处理任务限额。
- **FILE-006**: backend/migrations/、backend/app/core/migrations.py、backend/scripts/migrate_database.py：显式版本化迁移与历史库收编。
- **FILE-007**: deploy/data_backup.py：PostgreSQL、上传目录和 BM25 索引的成套备份/隔离恢复。
- **FILE-008**: .github/workflows/ci.yml、backend/tests/test_resource_quotas.py、backend/tests/test_migrations_postgres.py、backend/tests/test_backup_layout.py：自动化验证。
- **FILE-009**: backend/app/services/durable_handoff_service.py、backend/app/schemas/agent_runtime.py、backend/app/api/routes/agent_runtime.py、frontend/src/components/tool-trace-panel.tsx：相同 Durable 计划指纹、结果复用提示和失败重试确认。
- **FILE-010**: scripts/third_batch_acceptance.py、scripts/third_batch_capacity_probe.py：固定业务验收和受控容量探测入口。

## 6. Testing

- **TEST-001**: 在新虚拟环境安装全部 requirements，执行 pip check，导入认证、凭据和应用模块。
- **TEST-002**: 人为延慢同步解析，验证并行异步任务在解析结束前运行；批量失败清除文件；限制实际解析并发。
- **TEST-003**: 模拟数据库/Redis 故障，ready 返回 503，live 仍为 200；响应不泄露连接信息。
- **TEST-004**: 生产密钥/CORS 错配拒绝启动，登录/注册/退出 Cookie 策略一致；前端 lint/build 和浏览器回归通过。
- **TEST-005**: 真实托管服务和独立 Worker 正常，受控终止一个 Worker 后自动重启；浏览器完成在线模型问答及工具读取。
- **TEST-006**: 8 个并发请求竞争同一用户配额时只允许配置数量成功；取消/异常释放租约；失败上传和任务超限不产生半成品。
- **TEST-007**: 空库升级、现有库备份后收编、目标表冲突失败回滚，以及重复升级保留业务行。
- **TEST-008**: 当前数据库备份到 `aiws-backups/20261017-batch2-current`；恢复到独立库后，用户数 13、知识库 Chunk 1739、上传文件 521、索引文件 3，并可执行数据库查询。
- **TEST-009**: CI 固定执行后端 PostgreSQL/Redis 集成、编译、前端 lint/build/Node 测试；本机最终回归 707 tests OK；Tavily、高德、在线模型等收费 API 不在 CI 自动调用。
- **TEST-010**: 第三批固定业务验收 5/5 通过：本地 MCP 带来源研究链、独立分支部分失败、Diff/Approval + Revision CAS、Durable 成功回写和 Durable 失败回写；命令为 `PYTHONPATH=backend backend/.venv/bin/python scripts/third_batch_acceptance.py`。
- **TEST-011**: `/health/ready` 受控并发探测 5/10/20 均完成 100%；首响应 p50 分别为 37.67ms、63.70ms、108.86ms，依赖检查 PostgreSQL/Redis 全部 `ok`。该结果只反映当前本机就绪接口，不是 Chat 吞吐 SLA。

## 7. Risks & Assumptions

- **RISK-001**: 线程隔离解决事件循环阻塞，但不能强行终止正在运行的 Python 解析器，也不替代第二批用户级限额；不宣称完全隔离恶意文件 CPU 消耗。
- **RISK-002**: Supervisor 负责子进程重启，不等于主机重启自动恢复；正式部署需要外层 systemd 和 HTTPS 入口，实际启用情况须单独记录。
- **ASSUMPTION-001**: 只验收已经运行的本地服务，不把部署示例当作公网生产上线证明。
- **RISK-003**: 第三批容量探测不调用模型，不能推出在线 Chat、RAG 或 Tool DAG 的实际容量；TASK-014 的真实外部用户试用尚未完成，不用固定测试账号冒充用户证据。

## 8. Related Specifications / Further Reading

- [当前技术架构](../docs/161_技术架构与运行_2026-09-28.md)
- [Supervisor 配置](https://supervisord.org/configuration.html)
- [FastAPI 部署概念](https://fastapi.tiangolo.com/deployment/concepts/)
