# 独立脚本沙箱部署

该组件只运行仓库审核的 Python 入口，不提供任意代码、Shell 或公共发布。它用于把资料处理、批量统计、格式转换等脚本与 Web、数据库及业务凭据隔开；脚本只拿本次授权的副本，产物检查后再由用户保存。

当前服务器尚不能真实启用：缺少 `newuidmap/newgidmap`，没有独立 Worker 系统账号和 Rootless socket，CPU/内存/pids 控制器仍在 cgroup v1 下。禁止将 `/var/run/docker.sock` 交给 Web，禁止临时改为宿主 Python 执行。

## 2026-10-28 再次实机检查

| 检查项 | 实际结果 | 对验收的影响 |
| --- | --- | --- |
| 当前运行身份 | `ligengnan`，UID 1007，与 Web 相同 | 不能充当独立沙箱 Worker |
| 独立账号 | `getent passwd aiws-sandbox` 无结果 | 需管理员创建并配置，不能伪造 `AIWS_WEB_UID` 绕过检查 |
| UID/GID 映射程序 | `newuidmap/newgidmap` 均不存在；RootlessKit 实际启动失败 | 下载到用户目录也不能获得必要的宿主权限，需安装系统 uidmap |
| 宿主 Docker | `/var/run/docker.sock` 为 root/docker 所有；当前用户访问返回 permission denied | 不使用该服务，更不能修改共享 socket 权限 |
| Rootless socket | 当前用户运行目录未发现 Docker socket | 尚无可测试的独立 daemon |
| cgroup | 混合挂载：v2 位于 `/sys/fs/cgroup/unified`，但其中 `cgroup.controllers` 为空；cpu/memory/pids 均在 v1 挂载 | 仅看到 cgroup2 目录并不代表资源限制可用；必须提供实际具备控制器的 v2 环境 |
| 管理权限 | `sudo -n -l` 返回需要密码 | 当前自动化会话不能创建系统账号、安装系统映射程序或变更 cgroup |

当前用户的 user systemd 已有 Delegate=yes，但这不能把 v1 控制器自动转换为 v2；因此不是只补一个文件或重启 Worker 就能解决。未修改内核参数、启动项、共享 Docker 或系统账号，也未重启服务器。

管理员需要先完成三项宿主准备：安装发行版提供的 uidmap；创建独立非 root Worker 账号并配置 subuid/subgid；提供 CPU/memory/pids 控制器可委派的 cgroup v2 环境。共享服务器如需切换启动参数或重启，必须由管理员评估现有 Docker 与其他项目影响；也可以提供一台已具备这些条件的独立测试机。宿主准备完成后，项目侧继续负责部署 Rootless daemon、固定镜像、Worker 和真实业务验收，无需把管理员密码或业务密钥写进聊天。

真实验收完成的标准是：在同一个有效隔离环境内，实际跑通文件授权→容器执行→产物检查→确认保存，并验证资源、网络、超时和失联失败场景。当前阻断检查通过不等于这些真实容器场景通过。

## 部署顺序

1. 运维创建独立非 root Worker 账号，配置 subuid/subgid、`newuidmap/newgidmap`、RootlessKit/slirp4netns、cgroup v2 资源委派。Web 与该账号不同 UID，不能访问其运行目录或 socket。
2. 使用独立 Rootless Docker；RootlessKit 必须带 `--disable-host-loopback`。socket 仅 Worker 所有，权限 0600；不开放 TCP Docker API，不共享给 Web。Worker 会检查以上条件以及 CPU、memory、pids 限制和 seccomp；任何一项缺失立即退出。
3. 从已核对 digest 的 Python 官方基础镜像构建 `backend/sandbox/Dockerfile`，不从浮动标签隐式拉取；扫描并记录审核产物。将最终镜像存为带 sha256 digest 的本地镜像引用，运行参数 `--pull=never` 不自动更新。
4. 按 `sandbox-worker.env.example` 为独立账号设置 Web UID、Rootless socket 和固定镜像引用。数据库凭据由 Worker 单独配置，最小化数据库权限；这些环境不进入容器，也不进入 Web 配置。
5. 在该账号的进程管理器执行：`PYTHONPATH=backend python -m scripts.run_sandbox_worker`（工作目录为项目 backend 时省略 PYTHONPATH）。Web 的 Supervisor 不包含此服务，避免同账号假隔离。Worker 每 5 秒更新健康状态；30 秒无就绪心跳后，Web 立即停止接受新任务确认。
6. 在独立环境跑真实验证后才能宣布沙箱上线：选择文件、两阶段授权、执行、输出检查、保存新草稿；测试直接联网失败、非授权域名/重定向/内网 DNS 拒绝、超时、输出洪泛、进程/内存耗尽、Worker 崩溃与旧结果拒绝入库。当前单测不能替代这些验证。

## 数据与执行边界

Web API 仅保存授权计划与队列状态，不导入 `sandbox.runner`。Worker 从数据库领取；容器只收到审核脚本和授权文件的正文副本，不挂载用户目录、数据库、Redis、模型凭据、宿主环境或 Docker socket。复制的输入只读，根目录只读，临时目录采用限额 tmpfs；每任务销毁容器与网络。当前参数为单任务 CPU 1、256 MiB 内存、64 个进程、脚本 45 秒、stdout 1 MiB、输入 8 MiB、导入产物 8 MiB；代理单独有同等容器资源限制。依赖安装环境也有相同限额，大型依赖会明确失败。

任务默认 `--network=none`；需联网时只连内部网络。独立出口代理连出口网络，任务禁用外部 DNS，仅能使用代理 IP。代理只接受用户授权的完整域名和 HTTPS 443；每次 CONNECT 解析并检查所有 IP，再直连检查后的 IP，域名重定向需重新授权，内网/本机/保留地址拒绝。没有 host 网络和任意 HTTP 写工具。容器隔离和域名控制仍不能证明能安全运行任意恶意程序，首版只开放审核脚本。

## 依赖授权

1. 用户在预览看到直接依赖 `包名==版本`、安装阶段域名 PyPI/files.pythonhosted.org、任务阶段域名及授权文件。
2. 初次批准后，只在不含资料的安装解析容器运行 pip 的 binary-only dry-run；完整传递依赖、精确版本、wheel URL 和摘要返回 `awaiting_dependency_approval`。
3. 用户第二次批准后，重新检查插件版本和授权脚本，安装容器仅从已审核官方包源下载已确认 wheel，校验 SHA256，再 `--no-index --require-hashes` 安装；不接受 Git、直接 URL、sdist、可编辑依赖或系统包，不在任务期间自行安装。
4. 任务只拿到安装好的只读依赖副本；安装失败终止，不减少检查。首个可选实例是资料清单的 `tabulate==0.9.0`。

## 产物与恢复

PID 1 等待脚本完成后保留 tmpfs 一小段时间，让 Worker 在容器仍运行时提取产物；不能等容器退出后再复制 tmpfs。归档拒绝路径穿越、链接、设备文件、嵌套目录、非文本、超量产物和程序文件。验证后存为用户作用域的任务产物；用户再显式选择保存为工作区新草稿，不能覆盖已有文件。

任务租约 180 秒，写回核对 owner/version/expiry，并重新核验插件仍在项目启用、版本未变化。过期任务标记 failed，不自动无限重试；用户重新预览后再创建新任务。不能宣传为任意 Python 状态 checkpoint 或自动恢复执行到某一行。
