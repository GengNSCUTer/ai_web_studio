"""仅独立 Worker 导入的 Docker 执行器；任何检查失败均不回退宿主运行。"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
import re
import selectors
import signal
import socket
import stat
import subprocess
import tarfile
import time
from uuid import uuid4

from app.services.sandbox_policy import LIMITS, SCRIPTS, SandboxPolicyError, validate_wheel_lock, script_digest


class SandboxUnavailable(RuntimeError):
    pass


class DockerSandbox:
    def __init__(self):
        self.socket_path = Path(os.environ.get("AIWS_SANDBOX_DOCKER_SOCKET", "/nonexistent"))
        self.image = os.environ.get("AIWS_SANDBOX_IMAGE", "")
        self.web_uid = os.environ.get("AIWS_WEB_UID", "")
        # 不继承数据库/Redis/模型配置、代理、DOCKER_CONTEXT 或用户 pip 配置。
        self.env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "DOCKER_HOST": f"unix://{self.socket_path}"}

    def docker(self, args, *, input_bytes=None, timeout=15, max_bytes=1024 * 1024):
        process = subprocess.Popen(["docker", *args], stdin=subprocess.PIPE if input_bytes is not None else subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=self.env, start_new_session=True)
        if input_bytes is not None:
            # 输入 tar 本身已有限额；使用线程写入，防止管道填满时不能执行总超时。
            import threading
            def send():
                try:
                    process.stdin.write(input_bytes)
                    process.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
            threading.Thread(target=send, daemon=True).start()
        selector = selectors.DefaultSelector()
        selector.register(process.stdout, selectors.EVENT_READ)
        chunks = []
        size = 0
        deadline = time.monotonic() + timeout
        try:
            while selector.get_map():
                if time.monotonic() >= deadline:
                    raise SandboxUnavailable("沙箱运行超时。")
                for key, _ in selector.select(min(0.2, max(0, deadline - time.monotonic()))):
                    data = os.read(key.fd, 65536)
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    size += len(data)
                    if size > max_bytes:
                        raise SandboxUnavailable("沙箱输出超过限额。")
                    chunks.append(data)
            if process.wait(timeout=max(0.1, deadline - time.monotonic())):
                # 不把 Docker 错误中的主机路径或授权文档输出写入应用日志。
                raise SandboxUnavailable("容器操作失败；请在独立 Worker 检查运行环境。")
            return b"".join(chunks)
        finally:
            selector.close()
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            process.stdout.close()

    def preflight(self):
        if not self.web_uid.isdigit() or int(self.web_uid) in (0, os.getuid()) or os.getuid() == 0:
            raise SandboxUnavailable("沙箱 Worker 必须与 Web 使用不同的非 root 系统账号。")
        try:
            info = self.socket_path.stat()
        except FileNotFoundError as exc:
            raise SandboxUnavailable("独立 Rootless Docker socket 不存在；禁止使用宿主 Docker。") from exc
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise SandboxUnavailable("Rootless socket 必须由 Worker 独占，权限为 0600。")
        if not re.fullmatch(r"[a-zA-Z0-9/_.:-]+@sha256:[a-f0-9]{64}", self.image):
            raise SandboxUnavailable("必须指定已审核、本地预装的沙箱镜像 digest。")
        runtime = json.loads(self.docker(["info", "--format", "{{json .}}"], max_bytes=512000))
        security = " ".join(runtime.get("SecurityOptions", []))
        if "rootless" not in security or "seccomp" not in security or str(runtime.get("CgroupVersion")) != "2":
            raise SandboxUnavailable("要求 Rootless、默认 seccomp 和 cgroup v2。")
        if any(runtime.get(key) is not True for key in ("MemoryLimit", "PidsLimit", "CPUCfsQuota")):
            raise SandboxUnavailable("Docker 无法强制执行 CPU、内存或进程限额。")
        protected_network = False
        for proc in Path("/proc").iterdir():
            if proc.name.isdigit():
                try:
                    if proc.stat().st_uid != os.getuid():
                        continue
                    command = (proc / "cmdline").read_bytes().split(b"\0")
                    if command and Path(command[0].decode()).name == "rootlesskit" and b"--disable-host-loopback" in command:
                        protected_network = True
                except (OSError, UnicodeError):
                    continue
        if not protected_network:
            raise SandboxUnavailable("未发现禁用宿主 loopback 转发的 RootlessKit；拒绝联网沙箱。")
        self.docker(["image", "inspect", self.image], max_bytes=512000)
        return "rootless/seccomp/cgroup-v2/image 就绪"

    def create_args(self, name, *, network="none", env=None):
        args = ["create", "--name", name, "--pull=never", "--read-only", "--user", "65532:65532",
                "--cap-drop=ALL", "--security-opt=no-new-privileges:true", "--pids-limit=64",
                "--cpus=1", "--memory=256m", "--memory-swap=256m", "--ulimit", "nofile=128:128",
                "--network", network, "--ipc=none", "--log-driver=none",
                "--tmpfs", "/tmp:rw,noexec,nosuid,nodev,size=16m,mode=1777",
                "--tmpfs", "/out:rw,noexec,nosuid,nodev,size=32m,mode=1777",
                "--tmpfs", "/deps:rw,noexec,nosuid,nodev,size=16m,mode=1777"]
        for key, value in (env or {}).items():
            args.extend(["--env", f"{key}={value}"])
        return args

    @staticmethod
    def local_addresses():
        """把宿主接口地址加入出口拒绝表，包括宿主自身的公网接口地址。"""
        import fcntl
        import struct
        addresses = set(os.environ.get("AIWS_SANDBOX_DENIED_IPS", "").split(",")) - {""}
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
            for _, name in socket.if_nameindex():
                try:
                    data = fcntl.ioctl(connection.fileno(), 0x8915, struct.pack("256s", name.encode()[:15]))
                    addresses.add(socket.inet_ntoa(data[20:24]))
                except OSError:
                    pass
        try:
            for line in Path("/proc/net/if_inet6").read_text().splitlines():
                addresses.add(socket.inet_ntop(socket.AF_INET6, bytes.fromhex(line.split()[0])))
        except OSError:
            pass
        return sorted(addresses)

    @staticmethod
    def input_tar(files, script):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as archive:
            manifest = json.dumps({f["id"]: f.get("name", f["id"]) for f in files}, ensure_ascii=False).encode()
            for name, content in [("main.py", script), ("input/manifest.json", manifest), *[(f"input/{f['id']}.txt", f["text"].encode()) for f in files]]:
                # 不使用原文件名/存储路径，防止挂载或 tar 路径穿越。
                if not re.fullmatch(r"(?:main\.py|input/manifest\.json|input/[a-zA-Z0-9-]{1,36}\.txt)", name):
                    raise SandboxPolicyError("授权文件 ID 格式无效。")
                item = tarfile.TarInfo(name)
                item.mode = 0o444
                item.size = len(content)
                archive.addfile(item, io.BytesIO(content))
        return buffer.getvalue()

    @staticmethod
    def inspect_artifacts(raw):
        results = []
        with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
            total = 0
            for item in archive:
                if item.isdir():
                    continue
                name = Path(item.name)
                if not item.isfile() or name.is_absolute() or ".." in name.parts or len(name.parts) != 2 or name.parts[0] != "out":
                    raise SandboxPolicyError("产物包含链接、特殊文件或嵌套路径。")
                if name.name in {".exit.json", ".stdout.txt"}:
                    continue
                if name.suffix not in {".md", ".txt", ".json", ".csv"}:
                    raise SandboxPolicyError("首版沙箱仅导入文本产物，不接受可执行文件。")
                total += item.size
                if total > LIMITS["artifact_bytes"] or len(results) >= 16:
                    raise SandboxPolicyError("产物超过数量或大小限额。")
                text = archive.extractfile(item).read().decode("utf-8")
                if "\x00" in text:
                    raise SandboxPolicyError("产物不是有效文本。")
                results.append({"name": name.name, "content": text})
        return results

    def resolve_dependencies(self, plan):
        """安装解析不携带任务资料；完整版本与 wheel 摘要返回用户二次授权。"""
        requirements = plan["dependencies"]
        from app.services.sandbox_policy import validate_requirement
        for value in requirements:
            validate_requirement(value)
        code = ("import subprocess\nsubprocess.run(['python','-m','pip','install','--dry-run',"
                "'--ignore-installed','--only-binary=:all:','--disable-pip-version-check',"
                "'--no-cache-dir','--index-url','https://pypi.org/simple','--report','/out/lock.json',"
                f"*{requirements!r}], check=True)\n")
        result = self._run({**plan, "files": [], "domains": ["pypi.org", "files.pythonhosted.org"]}, code.encode())
        report = next((x["content"] for x in result["artifacts"] if x["name"] == "lock.json"), None)
        if not report:
            raise SandboxPolicyError("解析未返回完整依赖报告。")
        lock = validate_wheel_lock(json.loads(report))
        normalized = {x["requirement"].lower().replace("_", "-") for x in lock}
        if not {x.lower().replace("_", "-") for x in requirements}.issubset(normalized):
            raise SandboxPolicyError("完整解析结果缺少用户选定的直接依赖。")
        return lock

    def install_dependencies(self, plan):
        """只下载已确认 wheel，核验摘要后离线安装；安装容器不含用户资料。"""
        lock = plan.get("wheel_lock", [])
        if not lock:
            raise SandboxPolicyError("缺少已确认依赖锁。")
        # 再次走同一验证，不信任存储被修改后的 URL、版本或摘要。
        validate_wheel_lock({"install": [{"download_info": {"url": x["url"], "archive_info": {"hashes": {"sha256": x["sha256"]}}},
            "metadata": dict(zip(("name", "version"), x["requirement"].split("==")))} for x in lock]})
        code = '''import hashlib, pathlib, subprocess, urllib.request
lock = LOCK
root = pathlib.Path('/tmp/wheels'); root.mkdir()
requirements = []
total = 0
for i, item in enumerate(lock):
    name = item['url'].split('/')[-1]
    if not name.endswith('.whl') or '/' in name or '?' in name:
        raise ValueError('wheel name')
    target = root / name
    digest = hashlib.sha256()
    with urllib.request.urlopen(item['url'], timeout=10) as response, target.open('wb') as output:
        if response.url != item['url']:
            raise ValueError('redirected wheel')
        while True:
            data = response.read(65536)
            if not data: break
            total += len(data)
            if total > 12 * 1024 * 1024: raise ValueError('wheel bytes')
            output.write(data); digest.update(data)
    if digest.hexdigest() != item['sha256']: raise ValueError('wheel hash')
    requirements.append(item['requirement'] + ' --hash=sha256:' + item['sha256'])
pathlib.Path('/tmp/requirements.txt').write_text('\\n'.join(requirements))
subprocess.run(['python','-m','pip','install','--no-index','--no-cache-dir','--no-compile',
    '--only-binary=:all:','--require-hashes','--find-links',str(root),
    '--target','/out/site','-r','/tmp/requirements.txt'], check=True)
'''.replace('LOCK', repr(lock))
        return self._run({**plan, "files": [], "domains": ["files.pythonhosted.org"]}, code.encode(), dependency_export=True)

    def execute(self, plan):
        self.preflight()
        script = SCRIPTS.get(plan["script"])
        if not script:
            raise SandboxPolicyError("未知脚本。")
        if plan.get("script_digest") != script_digest(plan["script"]):
            raise SandboxPolicyError("审核脚本在授权后已变化。")
        dependencies = self.install_dependencies(plan) if plan.get("dependencies") else None
        source = Path(__file__).parent / "scripts" / script["file_name"]
        return self._run(plan, source.read_bytes(), dependency_tar=dependencies)

    def _run(self, plan, code, *, dependency_export=False, dependency_tar=None):
        self.preflight()
        name = "aiws-script-" + uuid4().hex
        proxy = name + "-egress"
        network = name + "-internal"
        try:
            env = {"PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1"}
            if plan["domains"]:
                # 任务只加入 internal 网络；代理独立加入出口网络，没有文件/凭据挂载。
                self.docker(["network", "create", "--internal", network])
                self.docker(self.create_args(proxy, network=network,
                    env={"ALLOWED_DOMAINS": json.dumps(plan["domains"]), "DENIED_IPS": json.dumps(self.local_addresses())}) + [self.image, "python", "-I", "/opt/aiws/egress_proxy.py"])
                self.docker(["network", "connect", "bridge", proxy])
                self.docker(["start", proxy])
                env.update({"HTTPS_PROXY": f"http://{proxy}:8080", "HTTP_PROXY": f"http://{proxy}:8080", "NO_PROXY": ""})
            # PID 1 在脚本结束后短暂保留 tmpfs，Worker 先提取再销毁。
            # 不能等容器退出后再 docker cp：退出时 tmpfs 已卸载，产物会丢失。
            supervisor = '''import subprocess,selectors,os,time,json,pathlib,signal
p=subprocess.Popen(['python','-I','-c',"import sys,runpy;sys.path.insert(0,'/opt/deps');runpy.run_path('/main.py',run_name='__main__')"],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,start_new_session=True)
s=selectors.DefaultSelector();s.register(p.stdout,selectors.EVENT_READ)
deadline=time.monotonic()+45;size=0;failed=False
with open('/out/.stdout.txt','wb') as output:
 while s.get_map():
  if time.monotonic()>deadline: failed=True;break
  for key,_ in s.select(.2):
   data=os.read(key.fd,65536)
   if not data:s.unregister(key.fileobj);continue
   size+=len(data)
   if size>1048576:failed=True;break
   output.write(data)
  if failed:break
if p.poll() is None and failed:os.killpg(p.pid,signal.SIGKILL)
try:code=p.wait(timeout=1)
except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);code=-1
pathlib.Path('/out/.exit.json').write_text(json.dumps({'code':code,'failed':failed}))
time.sleep(20)
'''
            launch = ["python", "-I", "-c", supervisor]
            args = self.create_args(name, network=network if plan["domains"] else "none", env=env)
            args.extend(["--dns", "127.0.0.1"])
            if plan["domains"]:
                template = '{{(index .NetworkSettings.Networks "' + network + '").IPAddress}}'
                proxy_ip = self.docker(["inspect", "--format", template, proxy]).decode().strip()
                # 禁用任务 DNS，代理使用已分配 IP，不让任务借 Docker DNS 绕行。
                args = self.create_args(name, network=network, env={**env,
                    "HTTPS_PROXY": f"http://{proxy_ip}:8080", "HTTP_PROXY": f"http://{proxy_ip}:8080"}) + ["--dns", "127.0.0.1"]
            self.docker(args + [self.image, *launch])
            self.docker(["cp", "-", f"{name}:/"], input_bytes=self.input_tar(plan["files"], code))
            if dependency_tar:
                self.docker(["cp", "-", f"{name}:/"], input_bytes=dependency_tar)
            self.docker(["start", name])
            deadline = time.monotonic() + LIMITS["seconds"] + 5
            state = None
            while time.monotonic() < deadline:
                status = self.docker(["exec", name, "python", "-I", "-c",
                    "import pathlib; p=pathlib.Path('/out/.exit.json');print(p.read_text() if p.exists() else 'null')"], timeout=3)
                state = json.loads(status)
                if state:
                    break
                time.sleep(0.25)
            if not state or state.get("code") != 0 or state.get("failed"):
                raise SandboxUnavailable("脚本退出失败或触发内存限制。")
            raw = self.docker(["cp", f"{name}:/out", "-"], max_bytes=LIMITS["artifact_bytes"] + 65536)
            if dependency_export:
                return self.validate_dependency_tar(raw)
            return {"artifacts": self.inspect_artifacts(raw)}
        finally:
            for container in (name, proxy):
                try:
                    self.docker(["rm", "-f", container])
                except SandboxUnavailable:
                    pass
            try:
                self.docker(["network", "rm", network])
            except SandboxUnavailable:
                pass

    @staticmethod
    def validate_dependency_tar(raw):
        """依赖目录只复制普通文件，强制只读，拒绝符号链接与 tar 路径逃逸。"""
        output = io.BytesIO()
        total = 0
        with tarfile.open(fileobj=io.BytesIO(raw)) as source, tarfile.open(fileobj=output, mode="w") as target:
            for item in source:
                path = Path(item.name)
                if path.parts == ("out",):
                    continue
                if str(path) in {"out/.exit.json", "out/.stdout.txt"} and item.isfile():
                    continue
                if path.is_absolute() or ".." in path.parts or path.parts[:2] != ("out", "site"):
                    raise SandboxPolicyError("依赖归档路径非法。")
                if not (item.isfile() or item.isdir()):
                    raise SandboxPolicyError("依赖包含链接或设备文件。")
                total += item.size
                if total > LIMITS["artifact_bytes"]:
                    raise SandboxPolicyError("依赖环境超过限额。")
                info = tarfile.TarInfo(str(Path("opt/deps", *path.parts[2:])))
                info.mode = 0o555 if item.isdir() else 0o444
                info.type = tarfile.DIRTYPE if item.isdir() else tarfile.REGTYPE
                info.size = item.size if item.isfile() else 0
                target.addfile(info, source.extractfile(item) if item.isfile() else None)
        return output.getvalue()
