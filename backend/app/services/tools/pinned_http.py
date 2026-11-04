"""MCP连接层DNS绑定：按验证过的IP连接，URL/Host/TLS SNI仍使用原域名。"""

import asyncio
import ipaddress
import socket
import ssl

import httpcore
import httpx

from app.services.tools.mcp_security import McpEndpointPolicyError, is_public_unicast_address


class PublicNetworkBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, *, allow_private=False, backend=None):
        self.allow_private = allow_private
        self.backend = backend or httpcore.AnyIOBackend()

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        async def connect():
            try:
                direct = ipaddress.ip_address(host)
                addresses = [str(direct)]
            except ValueError:
                try:
                    records = await asyncio.to_thread(socket.getaddrinfo, host, port, type=socket.SOCK_STREAM)
                except socket.gaierror:
                    raise httpcore.ConnectError("MCP目标解析失败。") from None
                addresses = list(dict.fromkeys(record[4][0] for record in records))
            if not addresses or (not self.allow_private and any(not is_public_unicast_address(address) for address in addresses)):
                raise McpEndpointPolicyError("MCP连接目标不是允许的公网地址。")
            # 原域名只保留在httpcore连接池origin/Host/TLS SNI中。底层收到的
            # 是已验证的数值IP，后续即使DNS改变也不能把该次连接改向私网。
            last = None
            for address in addresses:
                try:
                    return await self.backend.connect_tcp(address, port, timeout=timeout,
                        local_address=local_address, socket_options=socket_options)
                except (httpcore.ConnectError, httpcore.ConnectTimeout) as exc:
                    last = exc
            raise last or httpcore.ConnectError("MCP目标无法连接。")
        try:
            return await asyncio.wait_for(connect(), timeout) if timeout is not None else await connect()
        except asyncio.TimeoutError:
            raise httpcore.ConnectTimeout("MCP连接超时。") from None

    async def connect_unix_socket(self, *args, **kwargs):
        raise McpEndpointPolicyError("MCP不允许Unix Socket连接。")

    async def sleep(self, seconds):
        await asyncio.sleep(seconds)


class ResponseStream(httpx.AsyncByteStream):
    def __init__(self, stream):
        self.stream = stream

    async def __aiter__(self):
        try:
            async for chunk in self.stream:
                yield chunk
        except httpcore.TimeoutException:
            raise httpx.ReadTimeout("MCP响应读取超时。") from None
        except (httpcore.NetworkError, httpcore.ProtocolError):
            raise httpx.ReadError("MCP响应读取失败。") from None

    async def aclose(self):
        await self.stream.aclose()


class PinnedMcpTransport(httpx.AsyncBaseTransport):
    def __init__(self, *, allow_private=False):
        self.pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl.create_default_context(), network_backend=PublicNetworkBackend(allow_private=allow_private),
            max_connections=10, retries=0,
        )

    async def handle_async_request(self, request):
        core_request = httpcore.Request(method=request.method,
            url=httpcore.URL(scheme=request.url.raw_scheme, host=request.url.raw_host,
                             port=request.url.port, target=request.url.raw_path),
            headers=request.headers.raw, content=request.stream, extensions=request.extensions)
        try:
            response = await self.pool.handle_async_request(core_request)
        except httpcore.TimeoutException:
            raise httpx.ConnectTimeout("MCP连接或请求超时。") from None
        except (httpcore.NetworkError, httpcore.ProtocolError):
            raise httpx.ConnectError("MCP连接或协议失败。") from None
        return httpx.Response(response.status, headers=response.headers,
            stream=ResponseStream(response.stream), extensions=response.extensions)

    async def aclose(self):
        await self.pool.aclose()
