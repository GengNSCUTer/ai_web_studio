"""验证请求取消、额度隔离和拒绝前置；不依赖收费模型 API。"""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
import httpx
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import get_current_user
from app.api.routes import chat, uploads
from app.models.resource_lease import ResourceLease
from app.services import resource_quota_service as quota


class ResourceQuotaTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
        ResourceLease.__table__.create(self.engine)
        self.sessions = sessionmaker(self.engine)
        self.service = quota.ResourceQuotaService(self.sessions)

    def tearDown(self):
        self.engine.dispose()

    def test_limit_is_per_user_and_per_resource(self):
        token = self.service.acquire("a", "chat", 1)
        with self.assertRaises(HTTPException) as failure:
            self.service.acquire("a", "chat", 1)
        self.assertEqual(failure.exception.status_code, 429)
        self.assertEqual(failure.exception.headers["Retry-After"], "5")
        self.service.acquire("b", "chat", 1)
        self.service.acquire("a", "upload", 1)
        self.service.release(token)
        self.service.acquire("a", "chat", 1)

    def test_expired_lease_can_be_replaced_but_not_renewed(self):
        token = self.service.acquire("a", "chat", 1)
        with self.sessions() as db:
            db.get(ResourceLease, token).expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            db.commit()
        self.assertFalse(self.service.renew(token))
        replacement = self.service.acquire("a", "chat", 1)
        self.assertNotEqual(token, replacement)
        self.service.release(token)
        self.assertTrue(self.service.renew(replacement))

    def test_storage_counts_existing_files_and_concurrent_reservations(self):
        with TemporaryDirectory() as root, patch.object(quota, "settings", replace(quota.settings,
                upload_dir=root, user_upload_storage_bytes=10)):
            user_dir = Path(root) / "a"
            user_dir.mkdir()
            (user_dir / "existing.md").write_bytes(b"1234")
            first = self.service.acquire("a", "upload", 2)
            second = self.service.acquire("a", "upload", 2)
            self.service.reserve_upload(first, 4)
            with self.assertRaises(HTTPException):
                self.service.reserve_upload(second, 3)
            self.service.reserve_upload(second, 2)
            self.assertEqual(len(list(user_dir.iterdir())), 1)

    def test_capacity_is_released_on_body_error_and_cancellation(self):
        async def scenario():
            with self.assertRaisesRegex(ValueError, "business failure"):
                async with quota.resource_capacity("a", "chat", 1, service=self.service):
                    raise ValueError("business failure")
            entered = asyncio.Event()
            async def hold():
                async with quota.resource_capacity("a", "chat", 1, service=self.service):
                    entered.set()
                    await asyncio.Event().wait()
            task = asyncio.create_task(hold())
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            with self.sessions() as db:
                self.assertEqual(list(db.scalars(select(ResourceLease))), [])
        asyncio.run(scenario())

    def test_chat_limit_precedes_prepare_and_lasts_until_stream_finishes(self):
        async def scenario():
            app = FastAPI()
            app.include_router(chat.router)
            app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(id="a")
            entered, release = asyncio.Event(), asyncio.Event()
            async def prepare(*args):
                entered.set()
                return object()
            def response(*args, **kwargs):
                async def body():
                    yield b"first\n"
                    await release.wait()
                    yield b"done\n"
                return StreamingResponse(body())
            payload = {"content": "测试额度", "model_name": "test"}
            with patch.object(quota, "ResourceQuotaService", return_value=self.service), \
                    patch("app.api.resource_limits.settings", replace(quota.settings, user_chat_concurrency=1)), \
                    patch.object(chat.ChatExecutionService, "prepare_chat_execution", side_effect=prepare) as prepare_mock, \
                    patch.object(chat, "_build_streaming_response", side_effect=response):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                    first = asyncio.create_task(client.post("/chat/events-stream", json=payload))
                    await asyncio.wait_for(entered.wait(), 3)
                    rejected = await client.post("/chat/events-stream", json=payload)
                    self.assertEqual(rejected.status_code, 429)
                    self.assertEqual(prepare_mock.call_count, 1)
                    release.set()
                    self.assertEqual((await first).status_code, 200)
                    self.assertEqual((await client.post("/chat/events-stream", json=payload)).status_code, 200)
        asyncio.run(scenario())
