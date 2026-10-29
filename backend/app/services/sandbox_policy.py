"""纯数据策略；此模块不连接 Docker，也不执行用户脚本。"""

from __future__ import annotations

import ipaddress
import re
import socket
import hashlib
from pathlib import Path
from urllib.parse import urlsplit


class SandboxPolicyError(ValueError):
    pass


def normalize_domain(value: str) -> str:
    domain = value.strip().lower().rstrip(".")
    if len(domain) > 253 or not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", domain) or "." not in domain:
        raise SandboxPolicyError("必须逐项填写完整域名，不接受 URL、通配符、端口或 IP。")
    if any(not label or len(label) > 63 or label.startswith("-") or label.endswith("-") for label in domain.split(".")):
        raise SandboxPolicyError("域名格式无效。")
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        if domain.endswith((".local", ".localhost", ".internal", ".test", ".lan")):
            raise SandboxPolicyError("不能授权内网域名。")
        return domain
    raise SandboxPolicyError("不能直接授权 IP 地址。")


def public_addresses(domain: str) -> list[str]:
    """每次 CONNECT 解析后直接连接已检查 IP，避免检查与连接的 DNS 漂移。"""
    domain = normalize_domain(domain)
    addresses = list(dict.fromkeys(row[4][0] for row in socket.getaddrinfo(domain, 443, type=socket.SOCK_STREAM)))
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise SandboxPolicyError("目标 DNS 包含本机、内网或保留地址。")
    return addresses


def validate_requirement(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}==[0-9][A-Za-z0-9_.+!-]{0,49}", value):
        raise SandboxPolicyError("依赖必须为包名==精确版本；不支持 Git、URL、可编辑或系统包。")
    return value


def validate_wheel_lock(report: dict) -> list[dict]:
    """仅接受固定包源的二进制 wheel，完整传递依赖也必须进入授权清单。"""
    rows = report.get("install")
    if not isinstance(rows, list) or len(rows) > 100:
        raise SandboxPolicyError("依赖解析清单不完整或过大。")
    result = []
    for row in rows:
        info = row.get("download_info", {})
        url = urlsplit(info.get("url", ""))
        meta = row.get("metadata", {})
        digest = info.get("archive_info", {}).get("hashes", {}).get("sha256", "")
        requirement = validate_requirement(f"{meta.get('name', '')}=={meta.get('version', '')}")
        if (url.scheme != "https" or url.hostname != "files.pythonhosted.org" or url.username or url.password
                or url.port not in (None, 443) or not url.path.endswith(".whl") or not re.fullmatch(r"[a-f0-9]{64}", digest)):
            raise SandboxPolicyError("只允许官方 PyPI wheel 及其 SHA256，不安装源码构建。")
        result.append({"requirement": requirement, "url": info["url"], "sha256": digest})
    return result


LIMITS = {"cpus": "1", "memory": "256m", "pids": 64, "seconds": 45,
          "output_bytes": 1024 * 1024, "artifact_bytes": 8 * 1024 * 1024, "input_bytes": 8 * 1024 * 1024,
          "disk_bytes": 64 * 1024 * 1024}

# 只能运行仓库内经过代码审核的入口，客户端提交任意 Python 不在首版范围内。
SCRIPTS = {"document_inventory": {"file_name": "document_inventory.py", "domains": [], "dependencies": ["tabulate==0.9.0"]}}


def script_digest(script_key: str) -> str:
    """绑定用户确认的审核脚本正文，阻止预览后同版本脚本被替换再执行。"""
    script = SCRIPTS.get(script_key)
    if not script:
        raise SandboxPolicyError("未知脚本。")
    source = Path(__file__).resolve().parents[2] / "sandbox/scripts" / script["file_name"]
    return hashlib.sha256(source.read_bytes()).hexdigest()
