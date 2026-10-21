from __future__ import annotations

import asyncio
from dataclasses import replace
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx
import anyio
from openai import APIStatusError

from app.services.chat_provider_service import ChatProviderService
from app.services.ollama_service import OllamaService
from app.services import provider_client_pool as pool_module
from app.services.provider_client_pool import (
    ProviderClientPool,
    close_provider_client_pool,
    get_provider_client_pool,
    start_provider_client_pool,
)


class ProviderClientPoolTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.writers = set()
        self.requests = []
        self.faults = []
        self.first_bytes = asyncio.Event()
        self.release_slow_stream = asyncio.Event()
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.base_url = f"http://127.0.0.1:{self.server.sockets[0].getsockname()[1]}"
        self.pools = []

    async def asyncTearDown(self):
        self.release_slow_stream.set()
        await close_provider_client_pool()
        for pool in self.pools:
            await pool.close_all()
        # Python 3.10 的 unittest 循环不会替测试排空 SDK 内部 SSE 异步生成器。
        await asyncio.get_running_loop().shutdown_asyncgens()
        self.server.close()
        await self.server.wait_closed()
        for writer in list(self.writers):
            writer.close()
        await asyncio.gather(*(writer.wait_closed() for writer in list(self.writers)), return_exceptions=True)

    def pool(self, **kwargs):
        pool = ProviderClientPool(**kwargs)
        self.pools.append(pool)
        return pool

    async def _handle(self, reader, writer):
        """轻量 HTTP/1.1 服务：保活、慢流、中断和错误均通过真实 TCP 返回。"""
        self.writers.add(writer)
        try:
            while True:
                raw = await reader.readuntil(b"\r\n\r\n")
                lines = raw.decode().split("\r\n")
                method, path, _ = lines[0].split(" ", 2)
                headers = dict(line.split(": ", 1) for line in lines[1:] if ": " in line)
                body = await reader.readexactly(int(headers.get("Content-Length", "0")))
                payload = json.loads(body) if body else {}
                self.requests.append((method, path, headers, writer.get_extra_info("peername")))
                if self.faults:
                    status = self.faults.pop(0)
                    data = b'{"error":{"message":"fixture error"}}'
                    writer.write(f"HTTP/1.1 {status} Error\r\nContent-Type: application/json\r\nContent-Length: {len(data)}\r\nRetry-After: 0.001\r\n\r\n".encode() + data)
                    await writer.drain()
                    continue
                if path.endswith("/models"):
                    data = json.dumps({"data": [{"id": "fixture-model"}]}).encode()
                elif path.endswith("/api/tags"):
                    data = b'{"models":[{"name":"fixture-model"}]}'
                elif path.endswith("/api/chat"):
                    if payload.get("stream"):
                        data = b'{"message":{"content":"ok"},"done":true}\n'
                    else:
                        data = b'{"message":{"content":"ok"}}'
                elif path.endswith("/messages"):
                    if payload.get("stream"):
                        event = {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "ok"}}
                        data = f"data: {json.dumps(event)}\n\n".encode()
                    else:
                        data = b'{"content":[{"type":"text","text":"ok"}]}'
                elif payload.get("stream"):
                    event = {"id": "fixture", "object": "chat.completion.chunk", "choices": [
                        {"index": 0, "delta": {"content": "partial"}, "finish_reason": None}
                    ]}
                    data = f"data: {json.dumps(event)}\n\n".encode()
                    if payload.get("model") == "broken":
                        writer.write(f"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nContent-Length: {len(data) + 100}\r\n\r\n".encode() + data)
                        await writer.drain()
                        return
                    if payload.get("model") == "slow":
                        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n")
                        writer.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
                        await writer.drain()
                        self.first_bytes.set()
                        await self.release_slow_stream.wait()
                        writer.write(b"e\r\ndata: [DONE]\n\n\r\n0\r\n\r\n")
                        await writer.drain()
                        continue
                    data += b"data: [DONE]\n\n"
                else:
                    data = b'{"choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}]}'
                writer.write(f"HTTP/1.1 200 OK\r\nContent-Type: {'text/event-stream' if payload.get('stream') else 'application/json'}\r\nContent-Length: {len(data)}\r\n\r\n".encode() + data)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            writer.close()
            await writer.wait_closed()
            self.writers.discard(writer)

    def stream(self, service, model="fixture-model"):
        return service.stream_chat_events(
            provider_type="openai-compatible", base_url=self.base_url + "/v1", api_key="fixture-key",
            model_name=model, messages=[{"role": "user", "content": "test"}],
            temperature=0.2, top_p=0.9, max_tokens=64,
        )

    async def test_separate_services_reuse_one_real_tcp_connection_in_application_pool(self):
        pool = start_provider_client_pool()
        for _ in range(2):
            models = await ChatProviderService().list_models(
                provider_type="openai-compatible", base_url=self.base_url + "/v1", api_key="fixture-key",
            )
            self.assertEqual(models, ["fixture-model"])
        self.assertEqual(pool.size, 1)
        self.assertEqual(self.requests[0][3], self.requests[1][3])

    async def test_credentials_urls_protocols_and_case_sensitive_paths_do_not_share_client(self):
        pool = self.pool()
        clients = []
        for provider, url, key in [
            ("openai-compatible", "/API/v1", "fixture-a"),
            ("openai-compatible", "/API/v1", "fixture-b"),
            ("openai-compatible", "/api/v1", "fixture-a"),
            ("vllm", "/API/v1", "fixture-a"),
        ]:
            async with pool.acquire_openai(provider_type=provider, base_url=self.base_url + url, api_key=key) as client:
                clients.append(client)
        self.assertEqual(len({id(client) for client in clients}), 4)
        async with pool.acquire_http(provider_type="openai-compatible", base_url=self.base_url + "/API/v1", api_key="fixture-a") as client:
            self.assertNotIn(client, clients)
        self.assertNotIn("fixture-a", repr(list(pool._entries)))

    async def test_config_key_change_sends_new_authorization(self):
        pool = self.pool()
        service = ChatProviderService(client_pool=pool)
        for key in ("fixture-old", "fixture-new"):
            await service.list_models(provider_type="openai-compatible", base_url=self.base_url + "/v1", api_key=key)
        self.assertEqual([request[2]["Authorization"] for request in self.requests], ["Bearer fixture-old", "Bearer fixture-new"])
        self.assertNotEqual(self.requests[0][3], self.requests[1][3])

    async def test_full_pool_waits_and_never_evicts_active_client(self):
        pool = self.pool(max_size=1)
        async def acquire_another():
            async with pool.acquire_http(provider_type="other", base_url=self.base_url, api_key=None):
                return True
        async with pool.acquire_http(provider_type="first", base_url=self.base_url, api_key=None) as first:
            task = asyncio.create_task(acquire_another())
            await asyncio.sleep(0.01)
            self.assertFalse(task.done())
            self.assertFalse(first.is_closed)
            self.assertEqual(pool.size, 1)
        self.assertTrue(await asyncio.wait_for(task, 1))
        self.assertTrue(first.is_closed)
        self.assertEqual(pool.size, 1)

    async def test_full_pool_times_out_and_waiter_cancel_does_not_leak_capacity(self):
        pool = self.pool(max_size=1, wait_seconds=0.02)
        async with pool.acquire_http(provider_type="first", base_url=self.base_url, api_key=None):
            with self.assertRaises(TimeoutError):
                async with pool.acquire_http(provider_type="other", base_url=self.base_url, api_key=None):
                    self.fail("容量已满，不应获取成功")
        async with pool.acquire_http(provider_type="other", base_url=self.base_url, api_key=None):
            self.assertEqual(pool.size, 1)

    async def test_cancelled_lease_is_released_under_anyio_cancel_scope(self):
        pool = self.pool(max_size=1)
        with anyio.CancelScope() as scope:
            async with pool.acquire_http(provider_type="first", base_url=self.base_url, api_key=None):
                scope.cancel()
                await anyio.sleep(1)
        async with pool.acquire_http(provider_type="other", base_url=self.base_url, api_key=None):
            self.assertEqual(pool.size, 1)

    async def test_idle_ttl_replaces_old_client_and_shutdown_closes_sdk_and_http_clients(self):
        pool = self.pool()
        async with pool.acquire_http(provider_type="http", base_url=self.base_url, api_key=None) as old:
            pass
        with patch.object(pool_module, "settings", replace(pool_module.settings, provider_client_idle_seconds=0.001)):
            await asyncio.sleep(0.005)
            async with pool.acquire_http(provider_type="http", base_url=self.base_url, api_key=None) as new:
                self.assertIsNot(old, new)
                self.assertTrue(old.is_closed)
        async with pool.acquire_openai(provider_type="vllm", base_url=self.base_url, api_key=None) as sdk:
            pass
        await pool.close_all()
        self.assertTrue(new.is_closed)
        self.assertTrue(sdk.is_closed())
        with self.assertRaises(RuntimeError):
            async with pool.acquire_http(provider_type="http", base_url=self.base_url, api_key=None):
                pass

    async def test_stream_early_close_keeps_other_stream_alive_and_client_reusable(self):
        pool = self.pool()
        service = ChatProviderService(client_pool=pool)
        first = self.stream(service, "slow")
        second = self.stream(service, "slow")
        self.assertEqual((await anext(first)).text, "partial")
        self.assertEqual((await anext(second)).text, "partial")
        await first.aclose()
        self.release_slow_stream.set()
        self.assertEqual([event async for event in second], [])
        self.assertEqual([event.text async for event in self.stream(service)], ["partial"])
        self.assertEqual(pool.size, 1)
        self.assertTrue(all(entry.active == 0 for entry in pool._entries.values()))

    async def test_midstream_disconnect_preserves_partial_event_without_replaying_request(self):
        pool = self.pool()
        partial = []
        with self.assertRaises(httpx.RemoteProtocolError):
            async for event in self.stream(ChatProviderService(client_pool=pool), "broken"):
                partial.append(event.text)
        self.assertEqual(partial, ["partial"])
        self.assertEqual(len(self.requests), 1)
        self.assertTrue(all(entry.active == 0 for entry in pool._entries.values()))

    async def test_cancel_inflight_stream_releases_lease_and_next_chat_still_succeeds(self):
        pool = self.pool()
        service = ChatProviderService(client_pool=pool)
        received = asyncio.Event()
        async def consume():
            async for _ in self.stream(service, "slow"):
                received.set()
        task = asyncio.create_task(consume())
        await asyncio.wait_for(received.wait(), 2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(all(entry.active == 0 for entry in pool._entries.values()))
        self.assertEqual([event.text async for event in self.stream(service)], ["partial"])

    async def test_existing_sdk_retry_policy_is_preserved_for_429_and_503_only(self):
        pool = self.pool()
        service = ChatProviderService(client_pool=pool)
        for status in (429, 503):
            before = len(self.requests)
            self.faults = [status]
            self.assertEqual(await service.list_models(provider_type="openai-compatible", base_url=self.base_url + "/v1", api_key="fixture-key"), ["fixture-model"])
            self.assertEqual(len(self.requests) - before, 2)
        for status in (400, 401, 403):
            before = len(self.requests)
            self.faults = [status]
            with self.assertRaises(APIStatusError):
                await service.list_models(provider_type="openai-compatible", base_url=self.base_url + "/v1", api_key="fixture-key")
            self.assertEqual(len(self.requests) - before, 1)

    async def test_shutdown_wakes_waiter_without_creating_new_client(self):
        pool = self.pool(max_size=1)
        async def wait_for_other():
            async with pool.acquire_http(provider_type="other", base_url=self.base_url, api_key=None):
                self.fail("关闭后不应继续获取")
        async with pool.acquire_http(provider_type="first", base_url=self.base_url, api_key=None):
            waiter = asyncio.create_task(wait_for_other())
            await asyncio.sleep(0.01)
            await pool.close_all()
            with self.assertRaisesRegex(RuntimeError, "已关闭"):
                await waiter

    async def test_anthropic_and_ollama_keep_request_timeouts_and_reuse_http_clients(self):
        pool = self.pool()
        service = ChatProviderService(client_pool=pool)
        kwargs = dict(provider_type="anthropic", base_url=self.base_url, api_key="fixture-key", model_name="fixture-model", messages=[{"role": "user", "content": "test"}], temperature=0.2, top_p=0.9, max_tokens=32)
        self.assertEqual(await service.complete_chat(**kwargs), "ok")
        self.assertEqual([event.text async for event in service.stream_chat_events(**kwargs) if event.type == "answer_delta"], ["ok"])
        self.assertEqual(self.requests[0][3], self.requests[1][3])
        ollama = OllamaService(base_url=self.base_url, client_pool=pool)
        self.assertEqual(await ollama.list_models(), ["fixture-model"])
        self.assertEqual(await ollama.complete_chat(model_name="fixture-model", messages=[]), "ok")
        self.assertEqual(self.requests[2][3], self.requests[3][3])
        self.assertEqual([event.text async for event in ollama.stream_chat_events(model_name="fixture-model", messages=[])], ["ok"])

    async def test_pool_cannot_be_reused_from_another_event_loop(self):
        pool = self.pool()
        async with pool.acquire_http(provider_type="http", base_url=self.base_url, api_key=None):
            pass
        async def other_loop():
            async with pool.acquire_http(provider_type="http", base_url=self.base_url, api_key=None):
                pass
        with self.assertRaisesRegex(RuntimeError, "事件循环"):
            await asyncio.to_thread(lambda: asyncio.run(other_loop()))

    async def test_web_lifespan_registers_and_removes_pool_even_after_error(self):
        from app.main import app, lifespan
        with patch("app.main.ensure_runtime_schema"), patch("app.main.dispose_async_engine", new_callable=AsyncMock) as dispose:
            with self.assertRaisesRegex(ValueError, "fixture"):
                async with lifespan(app):
                    self.assertIsNotNone(get_provider_client_pool())
                    raise ValueError("fixture")
            self.assertIsNone(get_provider_client_pool())
            dispose.assert_awaited_once()

    async def test_no_web_lifespan_does_not_register_persistent_pool(self):
        await ChatProviderService().list_models(provider_type="openai-compatible", base_url=self.base_url + "/v1", api_key="fixture-key")
        self.assertIsNone(get_provider_client_pool())
