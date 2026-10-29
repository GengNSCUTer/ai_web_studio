import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

# 同时支持仓库根目录与 backend 目录的 unittest 入口。
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from deploy.check_services import inspect_services


class ServiceCheckTest(unittest.TestCase):
    @patch("deploy.check_services.subprocess.run")
    @patch("deploy.check_services.httpx.get")
    def test_external_redis_does_not_trigger_false_alarm(self, get, run):
        get.return_value = httpx.Response(200, json={"checks": {}})
        states = "\n".join(f"{role} RUNNING pid 1" for role in
                           ("backend", "frontend", "knowledge-worker", "memory-worker", "durable-worker"))
        run.return_value = subprocess.CompletedProcess([], 3, stdout=states + "\nredis STOPPED", stderr="")
        self.assertTrue(inspect_services(backend="http://backend", frontend="http://frontend")["ok"])

    @patch("deploy.check_services.subprocess.run")
    @patch("deploy.check_services.httpx.get")
    def test_proxy_failure_is_reported_even_when_homepage_opens(self, get, run):
        # 按地址模拟故障，新增 Worker 探针不应耗尽固定的响应列表。
        get.side_effect = lambda url, **kwargs: httpx.Response(
            503 if url == "http://frontend/api/health/ready" else 200,
            json={"checks": {}},
        )
        run.return_value = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        report = inspect_services(backend="http://backend", frontend="http://frontend")
        self.assertTrue(report["checks"]["frontend"]["ok"])
        self.assertFalse(report["checks"]["frontend_proxy"]["ok"])
        self.assertFalse(report["ok"])
