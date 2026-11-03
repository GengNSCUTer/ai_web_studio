"""资源作用域、网络边界、正文限制、配置撤销和授权URI回归。"""

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models
from app.core.database import Base
from app.models.user import User
from app.models.project import Project
from app.models.tool_config import McpServer
from app.services.mcp_resource_service import McpResourceService, resource_uri
from app.services.tools.mcp_security import McpEndpointPolicyError


class ResourceServiceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", poolclass=StaticPool,
                                    connect_args={"check_same_thread": False})
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        with self.sessions() as db:
            db.add_all([User(id="u", email="mcp-resource@example.test"), User(id="other", email="mcp-other@example.test")]); db.flush()
            db.add_all([Project(id="p", user_id="u", name="P"), Project(id="q", user_id="u", name="Q")]); db.flush()
            db.add_all([McpServer(id="s", user_id="u", project_id="p", name="资料服务", server_key="docs",
                        url="https://docs.example.test/mcp", is_enabled=True, auth_type="none"),
                        McpServer(id="global", user_id="u", name="全局资料", server_key="global",
                        url="https://docs.example.test/mcp", is_enabled=True, auth_type="none")]); db.commit()
        self.db_patch = patch("app.services.mcp_resource_service.SessionLocal", self.sessions); self.db_patch.start()
        self.policy = patch("app.services.mcp_resource_service.enforce_mcp_endpoint_target_policy", new_callable=AsyncMock)
        self.policy_mock = self.policy.start()
        self.client = AsyncMock()
        self.client.protocol_version = "2026-07-28"; self.client.mode = "modern"; self.client.capabilities = {"resources": {}}
        self.client.list_resources.return_value = [{"uri": "file:///remote/paper.md", "name": "论文资料", "mimeType": "text/markdown"}]
        self.client.read_resource.return_value = {"contents": [{"uri": "file:///remote/paper.md", "mimeType": "text/markdown", "text": "保留期限是37天。"}]}
        self.client_patch = patch("app.services.mcp_resource_service.McpHttpClient", return_value=self.client); self.client_patch.start()
        self.service = McpResourceService(user_id="u", project_id="p")

    def tearDown(self):
        self.client_patch.stop(); self.policy.stop(); self.db_patch.stop(); self.engine.dispose()

    async def test_servers_only_include_owner_and_current_project_scope(self):
        self.assertEqual({row["id"] for row in self.service.servers()}, {"s", "global"})
        self.assertEqual({row["id"] for row in McpResourceService(user_id="u", project_id="q").servers()}, {"global"})
        self.assertEqual(McpResourceService(user_id="other", project_id=None).servers(), [])

    async def test_cross_user_project_and_disabled_server_are_rejected_before_network(self):
        for user, project in [("other", None), ("u", "q"), ("u", None)]:
            with self.assertRaises(HTTPException):
                await McpResourceService(user_id=user, project_id=project).read("s", "file:///remote/paper.md")
        with self.sessions() as db:
            db.get(McpServer, "s").is_enabled = False; db.commit()
        with self.assertRaises(HTTPException): await self.service.list("s")
        self.client.list_resources.assert_not_awaited()

    async def test_only_listed_uris_can_be_read_and_never_open_local_files(self):
        with self.assertRaises(HTTPException) as error:
            await self.service.read("s", "file:///etc/passwd")
        self.assertEqual(error.exception.status_code, 403)
        self.client.read_resource.assert_not_awaited()
        source = await self.service.read("s", "file:///remote/paper.md")
        self.assertEqual(source.display_text, "保留期限是37天。")
        self.assertIsNone(source.url)
        self.assertEqual(source.metadata["resource_uri"], "file:///remote/paper.md")

    async def test_private_endpoint_is_rejected_even_for_listed_resource(self):
        self.policy_mock.side_effect = McpEndpointPolicyError("private")
        with self.assertRaises(HTTPException) as error: await self.service.list("s")
        self.assertEqual(error.exception.status_code, 403)
        self.client.list_resources.assert_not_awaited()

    async def test_binary_empty_large_and_extra_uri_contents_are_not_evidence(self):
        for content in [{"uri": "file:///remote/paper.md", "blob": "YQ==", "mimeType": "image/png"},
                        {"uri": "file:///remote/paper.md", "text": " "},
                        {"uri": "file:///remote/paper.md", "text": "大" * 120001},
                        {"uri": "file:///other/private", "text": "不能越界"}]:
            self.client.read_resource.return_value = {"contents": [content]}
            with self.assertRaises(HTTPException) as error:
                await self.service.read("s", "file:///remote/paper.md")
            self.assertEqual(error.exception.status_code, 422)

    async def test_remote_failure_retains_other_successful_resource(self):
        self.client.read_resource.side_effect = [RuntimeError("request failed"), self.client.read_resource.return_value]
        sources, notices = await self.service.build_sources([
            {"server_id": "s", "uri": "file:///remote/paper.md"}, {"server_id": "global", "uri": "file:///remote/paper.md"}])
        self.assertEqual(len(sources), 1); self.assertEqual(len(notices), 1)

    async def test_revocation_during_read_prevents_injection(self):
        async def revoke(uri):
            with self.sessions() as db:
                db.get(McpServer, "s").is_enabled = False; db.commit()
            return {"contents": [{"uri": uri, "text": "旧授权返回的资料"}]}
        self.client.read_resource.side_effect = revoke
        with self.assertRaises(HTTPException): await self.service.read("s", "file:///remote/paper.md")

    async def test_endpoint_change_during_read_prevents_injection(self):
        async def change(uri):
            with self.sessions() as db:
                db.get(McpServer, "s").url = "https://changed.example.test/mcp"; db.commit()
            return {"contents": [{"uri": uri, "text": "旧端点资料"}]}
        self.client.read_resource.side_effect = change
        with self.assertRaises(HTTPException) as error: await self.service.read("s", "file:///remote/paper.md")
        self.assertEqual(error.exception.status_code, 409)

    async def test_revocation_after_read_before_prompt_is_checked_again(self):
        await self.service.read("s", "file:///remote/paper.md")
        with self.sessions() as db:
            db.get(McpServer, "s").is_enabled = False; db.commit()
        with self.assertRaises(HTTPException): await self.service.revalidate()

    async def test_duplicate_selections_are_only_read_once(self):
        await self.service.build_sources([{"server_id": "s", "uri": "file:///remote/paper.md"}] * 2)
        self.assertEqual(self.client.read_resource.await_count, 1)

    async def test_uri_control_characters_and_embedded_credentials_are_rejected(self):
        for uri in ["", "not-a-uri", "file:///a\n", "https://user:password@example.test/a"]:
            with self.assertRaises(HTTPException): resource_uri(uri)

    async def test_resources_list_does_not_include_remote_instructions_or_icons(self):
        self.client.list_resources.return_value[0].update({"icons": [{"src": "http://127.0.0.1"}], "instructions": "扩权"})
        listed = await self.service.list("s")
        self.assertNotIn("icons", listed["resources"][0]); self.assertNotIn("instructions", listed["resources"][0])
