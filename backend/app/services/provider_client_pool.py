from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
import hashlib
import time
from typing import Any

import httpx
import anyio
from openai import AsyncOpenAI, DefaultAsyncHttpxClient

from app.core.config import settings


@dataclass
class _ClientEntry:
    """客户端只在没有借用者时淘汰，关闭动作兼容 SDK 和 HTTPX。"""

    client: Any
    close: Callable[[], Awaitable[None]]
    active: int = 0
    last_used: float = 0.0


class ProviderClientPool:
    """当前事件循环内按协议、地址、凭证隔离的有界客户端池。"""

    def __init__(self, *, max_size: int | None = None, wait_seconds: float | None = None) -> None:
        self.max_size = settings.provider_client_pool_max_size if max_size is None else max_size
        self.wait_seconds = settings.provider_client_pool_wait_seconds if wait_seconds is None else wait_seconds
        if self.max_size < 1 or self.wait_seconds <= 0:
            raise ValueError("Provider 连接池容量和等待时长必须大于零")
        if (settings.provider_client_idle_seconds <= 0
                or settings.provider_http_max_connections < 1
                or not 0 <= settings.provider_http_max_keepalive_connections <= settings.provider_http_max_connections):
            raise ValueError("Provider 连接数和闲置回收配置无效")
        self._entries: OrderedDict[tuple[str, str, str, str], _ClientEntry] = OrderedDict()
        self._condition = asyncio.Condition()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._closed = False

    def _check_loop(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        elif self._loop is not loop:
            raise RuntimeError("Provider 客户端不能跨事件循环使用")

    @staticmethod
    def _key(kind: str, provider_type: str, base_url: str, api_key: str | None) -> tuple[str, str, str, str]:
        # URL 的路径可能区分大小写，不能把整个地址 lower；密钥只在内部以指纹作键。
        return (
            kind,
            provider_type,
            str(httpx.URL(base_url)),
            hashlib.sha256((api_key or "").encode("utf-8")).hexdigest(),
        )

    @staticmethod
    def _limits() -> httpx.Limits:
        return httpx.Limits(
            max_connections=settings.provider_http_max_connections,
            max_keepalive_connections=settings.provider_http_max_keepalive_connections,
            keepalive_expiry=30.0,
        )

    async def _acquire(self, key: tuple[str, str, str, str], factory: Callable[[], _ClientEntry]) -> _ClientEntry:
        self._check_loop()
        deadline = time.monotonic() + self.wait_seconds
        async with self._condition:
            while True:
                if self._closed:
                    raise RuntimeError("Provider 连接池已关闭")
                # 配置更新产生新键；旧配置只在空闲且到期时释放，不中断在途请求。
                now = time.monotonic()
                for old_key, old_entry in list(self._entries.items()):
                    if old_entry.active == 0 and now - old_entry.last_used >= settings.provider_client_idle_seconds:
                        self._entries.pop(old_key)
                        await old_entry.close()
                entry = self._entries.get(key)
                if entry is not None:
                    self._entries.move_to_end(key)
                    entry.active += 1
                    return entry
                if len(self._entries) >= self.max_size:
                    idle_key = next((k for k, e in self._entries.items() if e.active == 0), None)
                    if idle_key is not None:
                        await self._entries.pop(idle_key).close()
                if len(self._entries) < self.max_size:
                    entry = factory()
                    entry.active = 1
                    self._entries[key] = entry
                    return entry
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Provider 连接池繁忙，请稍后重试")
                # 所有配置均在使用时有界等待，不临时创建无限量额外客户端。
                try:
                    await asyncio.wait_for(self._condition.wait(), timeout=remaining)
                except asyncio.TimeoutError as exc:
                    raise TimeoutError("Provider 连接池繁忙，请稍后重试") from exc

    async def _release(self, entry: _ClientEntry) -> None:
        async with self._condition:
            entry.active -= 1
            entry.last_used = time.monotonic()
            self._condition.notify_all()

    @asynccontextmanager
    async def acquire_openai(self, *, provider_type: str, base_url: str, api_key: str | None) -> AsyncIterator[AsyncOpenAI]:
        def create() -> _ClientEntry:
            client = AsyncOpenAI(
                api_key=api_key or "sk-placeholder",
                base_url=base_url,
                http_client=DefaultAsyncHttpxClient(limits=self._limits()),
            )
            return _ClientEntry(client=client, close=client.close)

        entry = await self._acquire(self._key("openai", provider_type, base_url, api_key), create)
        try:
            yield entry.client
        finally:
            # Starlette 断连使用取消作用域，清理不能再次被同一作用域取消。
            with anyio.CancelScope(shield=True):
                await self._release(entry)

    @asynccontextmanager
    async def acquire_http(self, *, provider_type: str, base_url: str, api_key: str | None) -> AsyncIterator[httpx.AsyncClient]:
        def create() -> _ClientEntry:
            client = httpx.AsyncClient(timeout=None, limits=self._limits())
            return _ClientEntry(client=client, close=client.aclose)

        entry = await self._acquire(self._key("http", provider_type, base_url, api_key), create)
        try:
            yield entry.client
        finally:
            with anyio.CancelScope(shield=True):
                await self._release(entry)

    async def close_all(self) -> None:
        """Web 服务停止接收并排空请求后，关闭池并唤醒仍在等待的任务。"""
        self._check_loop()
        async with self._condition:
            self._closed = True
            entries = list(self._entries.values())
            self._entries.clear()
            self._condition.notify_all()
        results = await asyncio.gather(*(entry.close() for entry in entries), return_exceptions=True)
        if any(isinstance(result, BaseException) for result in results):
            raise RuntimeError("部分 Provider 连接未能正常关闭")

    @property
    def size(self) -> int:
        return len(self._entries)


# 只有显式启动的 Web lifespan 注册池，短脚本/Worker 不会遗留跨 asyncio.run 的连接。
_application_pools: dict[asyncio.AbstractEventLoop, ProviderClientPool] = {}


def start_provider_client_pool() -> ProviderClientPool:
    loop = asyncio.get_running_loop()
    if loop not in _application_pools:
        _application_pools[loop] = ProviderClientPool()
    return _application_pools[loop]


def get_provider_client_pool() -> ProviderClientPool | None:
    return _application_pools.get(asyncio.get_running_loop())


async def close_provider_client_pool() -> None:
    pool = _application_pools.pop(asyncio.get_running_loop(), None)
    if pool is not None:
        await pool.close_all()
