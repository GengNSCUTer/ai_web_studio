"""按用户与项目授权读取MCP文本资源，绝不把资源URI当作宿主文件或HTTP地址执行。"""

import asyncio
from dataclasses import dataclass
from urllib.parse import urlsplit

from fastapi import HTTPException

from app.core.database import SessionLocal
from app.repositories.project_repo import ProjectRepository
from app.repositories.tool_config_repo import ToolConfigRepository
from app.services.tools.credentials import ToolCredentialResolver
from app.services.tools.mcp_client import McpHttpClient
from app.services.tools.mcp_security import McpEndpointPolicyError, enforce_mcp_endpoint_target_policy, validate_mcp_endpoint_url
from app.services.tools.schemas import ExternalSource, redact_sensitive_text


def resource_uri(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 2048 or any(ord(c) < 32 for c in value):
        raise HTTPException(422, "资源URI无效。")
    try:
        parsed = urlsplit(value)
        if not parsed.scheme or parsed.username or parsed.password:
            raise ValueError()
    except ValueError:
        raise HTTPException(422, "资源URI无效。") from None
    return value


def mcp_endpoint(server, api_key):
    endpoint = server.url.replace("{api_key}", api_key or "")
    headers = ({"Authorization": f"Bearer {api_key}"} if server.auth_type == "bearer" and api_key
               else {"X-API-Key": api_key} if server.auth_type == "api_key_header" and api_key else {})
    return endpoint, headers


@dataclass(frozen=True)
class ResourceServer:
    id: str
    name: str
    endpoint: str
    headers: dict
    project_id: str | None


class McpResourceService:
    def __init__(self, *, user_id: str, project_id: str | None):
        self.user_id, self.project_id = user_id, project_id
        self._snapshots: dict[str, ResourceServer] = {}

    async def revalidate(self):
        for server_id, expected in self._snapshots.items():
            if await asyncio.to_thread(self._server, server_id) != expected:
                raise HTTPException(409, "MCP资料授权或配置已变化，请重新选择。")

    def _project(self, db):
        if self.project_id and not ProjectRepository(db).get_by_user(self.project_id, self.user_id):
            raise HTTPException(404, "Project not found")

    def servers(self):
        with SessionLocal() as db:
            self._project(db)
            rows = ToolConfigRepository(db).list_mcp_servers(user_id=self.user_id)
            return [{"id": row.id, "name": row.name} for row in rows
                    if row.is_enabled and (row.project_id is None or row.project_id == self.project_id)]

    def _server(self, server_id):
        # 连接资料取值后释放事务，不能持有连接等待远端网络。
        with SessionLocal() as db:
            self._project(db)
            row = ToolConfigRepository(db).get_mcp_server(user_id=self.user_id, server_id=server_id)
            if not row or not row.is_enabled or row.project_id not in {None, self.project_id}:
                raise HTTPException(404, "MCP server not found")
            resolved = ToolCredentialResolver(db).resolve(user_id=self.user_id,
                                                        provider_key=row.credential_provider or row.server_key)
            if (row.auth_type != "none" or "{api_key}" in row.url) and not resolved.api_key:
                raise HTTPException(400, "MCP服务凭据未配置或已禁用。")
            validate_mcp_endpoint_url(row.url, auth_type=row.auth_type)
            endpoint, headers = mcp_endpoint(row, resolved.api_key)
            return ResourceServer(row.id, row.name, endpoint, headers, row.project_id)

    async def _client(self, server_id):
        server = await asyncio.to_thread(self._server, server_id)
        try:
            await enforce_mcp_endpoint_target_policy(server.endpoint)
        except McpEndpointPolicyError:
            raise HTTPException(403, "MCP服务目标未通过网络地址检查。") from None
        return server, McpHttpClient(endpoint=server.endpoint, extra_headers=server.headers)

    async def list(self, server_id):
        server, client = await self._client(server_id)
        rows = await client.list_resources()
        resources = []
        for row in rows:
            try:
                uri = resource_uri(row.get("uri"))
            except HTTPException:
                continue
            resources.append({"uri": uri, "name": redact_sensitive_text(str(row.get("name") or uri))[:240],
                              "description": redact_sensitive_text(str(row.get("description") or ""))[:1000],
                              "mimeType": str(row.get("mimeType") or "")[:128]})
        return {"server_id": server.id, "server_name": server.name, "resources": resources,
                "protocol_version": client.protocol_version, "protocol_mode": client.mode,
                "resources_supported": "resources" in client.capabilities}

    async def read(self, server_id, uri):
        uri = resource_uri(uri)
        server, client = await self._client(server_id)
        listed = await client.list_resources()
        selected = next((row for row in listed if row.get("uri") == uri), None)
        if selected is None:
            raise HTTPException(403, "该资源不在当前授权服务的可见清单中。")
        result = await client.read_resource(uri)
        texts = []
        for item in result["contents"]:
            mime = item.get("mimeType") or selected.get("mimeType") or "text/plain"
            if (item.get("uri") != uri or not isinstance(item.get("text"), str) or not isinstance(mime, str)
                    or not (mime.startswith("text/") or mime in {"application/json", "application/xml"})):
                raise HTTPException(422, "首版仅支持选中URI的文本资源，不读取额外URI或二进制内容。")
            texts.append(item["text"])
        body = redact_sensitive_text("\n\n".join(texts)).strip()
        if not body or len(body) > 120000:
            raise HTTPException(422, "资源为空或超出文本大小限制。")
        # 等待期间配置可能被撤销；注入前再次核对作用域、端点和凭据快照。
        current = await asyncio.to_thread(self._server, server_id)
        if current != server:
            raise HTTPException(409, "MCP服务配置已变化，请重新选择资源。")
        self._snapshots[server_id] = current
        return ExternalSource(source_type="mcp_resource", provider="mcp_resource", title=redact_sensitive_text(
            str(selected.get("name") or server.name))[:240], display_text=body,
            metadata={"mcp_resource_server_id": server.id, "mcp_resource_server_name": server.name,
                      "resource_uri": uri, "protocol_version": client.protocol_version,
                      "protocol_mode": client.mode, "mime_type": selected.get("mimeType") or "text/plain"})

    async def build_sources(self, selections):
        sources, notices = [], []
        unique = list({(item["server_id"], item["uri"]): item for item in selections}.values())
        for selection in unique:
            try:
                sources.append(await asyncio.wait_for(self.read(selection["server_id"], selection["uri"]), 30))
            except HTTPException as exc:
                if exc.status_code in {403, 404, 409}:
                    raise
                notices.append("所选MCP资源未能读取；本轮未将其作为证据。")
            except RuntimeError:
                notices.append("所选MCP资源服务不可用；本轮未将其作为证据。")
            except asyncio.TimeoutError:
                notices.append("所选MCP资源读取超时；本轮未将其作为证据。")
        return sources, notices
