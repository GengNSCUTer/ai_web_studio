from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

from app.core.config import settings
from app.models.tool_config import McpTool


class McpEndpointPolicyError(ValueError):
    pass


def validate_mcp_endpoint_url(endpoint: str, *, auth_type: str | None = None) -> None:
    """Reject malformed URL forms before persisting a user-added MCP Server."""

    if not endpoint or any(ord(char) < 32 for char in endpoint):
        raise McpEndpointPolicyError("MCP URL 不能为空或包含控制字符。")
    try:
        parsed = urlsplit(endpoint)
        hostname = parsed.hostname
    except ValueError as exc:
        raise McpEndpointPolicyError("MCP URL 格式不合法。") from exc
    if parsed.scheme not in {"http", "https"}:
        raise McpEndpointPolicyError("MCP URL 只允许 http 或 https。")
    if not hostname:
        raise McpEndpointPolicyError("MCP URL 缺少主机名。")
    try:
        parsed.port
    except ValueError as exc:
        raise McpEndpointPolicyError("MCP URL 端口不合法。") from exc
    if parsed.username is not None or parsed.password is not None:
        raise McpEndpointPolicyError("MCP URL 不允许在 authority 中携带用户名或密码。")
    if parsed.fragment:
        raise McpEndpointPolicyError("MCP URL 不允许包含 fragment。")
    carries_credential = (auth_type is not None and auth_type != "none") or "{api_key}" in endpoint
    if carries_credential and parsed.scheme != "https":
        raise McpEndpointPolicyError("携带凭据的 MCP Server 必须使用 HTTPS。")


async def enforce_mcp_endpoint_target_policy(
    endpoint: str,
    *,
    allow_private: bool | None = None,
) -> None:
    """Best-effort SSRF guard immediately before an outbound MCP request.

    This resolves all current A/AAAA targets and fails closed if any address is
    not globally routable. It reduces ordinary private-network/metadata SSRF,
    while connection-level DNS pinning would still be needed to fully eliminate
    DNS-rebinding TOCTOU attacks.
    """

    validate_mcp_endpoint_url(endpoint)
    effective_allow_private = (
        allow_private if allow_private is not None else settings.allow_private_mcp_servers
    )
    if effective_allow_private:
        return

    parsed = urlsplit(endpoint)
    hostname = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if hostname.lower() == "localhost" or hostname.lower().endswith(".localhost"):
        raise McpEndpointPolicyError("MCP Server 不允许访问本机或私网地址。")

    try:
        direct_ip = ipaddress.ip_address(hostname)
        addresses = {direct_ip}
    except ValueError:
        try:
            records = await asyncio.to_thread(socket.getaddrinfo, hostname, port, type=socket.SOCK_STREAM)
        except socket.gaierror as exc:
            raise McpEndpointPolicyError("MCP Server 主机名无法解析。") from exc
        addresses = {ipaddress.ip_address(record[4][0]) for record in records}

    if not addresses or any(not address.is_global for address in addresses):
        raise McpEndpointPolicyError("MCP Server 不允许访问本机、私网、保留地址或云元数据地址。")


def apply_remote_tool_security_policy(
    *,
    tool: McpTool,
    input_schema_json: str,
    output_schema_json: str,
    annotations_json: str,
) -> None:
    """Apply fail-closed local policy to untrusted MCP discovery metadata.

    Remote ``readOnlyHint`` is useful review context, but it is not an
    authorization statement. A first discovery, an unreviewed tool, or a
    remote input/output Schema or annotation change invalidates local approval.
    """

    security_metadata_changed = bool(
        tool.last_seen_at
        and (
            tool.input_schema_json != input_schema_json
            or tool.output_schema_json != output_schema_json
            or tool.annotations_json != annotations_json
        )
    )
    tool.input_schema_json = input_schema_json
    tool.output_schema_json = output_schema_json
    tool.annotations_json = annotations_json
    if not tool.risk_reviewed or security_metadata_changed:
        tool.read_only = False
        tool.risk_level = "high"
        tool.risk_reviewed = False
        tool.is_enabled = False
