"""仅允许授权 HTTPS 域名的出口；不向任务暴露宿主代理或 Docker API。"""

import ipaddress
import json
import os
import select
import socket
import socketserver
import time


def resolve_public(host, denied=()):
    addresses = list(dict.fromkeys(x[4][0] for x in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)))
    if not addresses or any(not ipaddress.ip_address(ip).is_global or ip in denied for ip in addresses):
        raise ValueError("非公开目标")
    return addresses


class Tunnel(socketserver.StreamRequestHandler):
    def handle(self):
        remote = None
        try:
            self.connection.settimeout(5)
            request = self.rfile.readline(2049)
            if len(request) > 2048:
                raise ValueError("请求行超限")
            method, authority, version = request.decode("ascii").strip().split(" ")
            if method != "CONNECT" or version not in {"HTTP/1.0", "HTTP/1.1"} or not authority.endswith(":443"):
                raise ValueError("仅允许 CONNECT 443")
            host = authority[:-4].lower()
            if host not in self.server.allowed_domains:
                raise ValueError("域名未授权")
            headers_size = 0
            while True:
                line = self.rfile.readline(2049)
                headers_size += len(line)
                if not line or headers_size > 8192:
                    raise ValueError("请求头超限")
                if line == b"\r\n":
                    break
            # 直接连接已检查 IP；重定向会引起新 CONNECT，仍须检查授权与 DNS。
            remote = socket.create_connection((resolve_public(host, self.server.denied_ips)[0], 443), timeout=5)
            self.wfile.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            self.wfile.flush()
            sockets = [self.connection, remote]
            deadline = time.monotonic() + 120
            transferred = 0
            while time.monotonic() < deadline:
                ready, _, _ = select.select(sockets, [], [], 2)
                for source in ready:
                    data = source.recv(65536)
                    if not data:
                        return
                    transferred += len(data)
                    if transferred > 32 * 1024 * 1024:
                        return
                    (remote if source is self.connection else self.connection).sendall(data)
        except Exception:
            try:
                self.wfile.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            except OSError:
                pass
        finally:
            if remote:
                remote.close()


class Proxy(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 8

    def verify_request(self, request, client_address):
        # 每个代理仅服务同任务容器，限制连接数由容器 pids 和出口进程共同约束。
        import threading
        return threading.active_count() < 16


if __name__ == "__main__":
    with Proxy(("0.0.0.0", 8080), Tunnel) as server:
        server.allowed_domains = set(json.loads(os.environ.get("ALLOWED_DOMAINS", "[]")))
        server.denied_ips = set(json.loads(os.environ.get("DENIED_IPS", "[]")))
        server.serve_forever()
