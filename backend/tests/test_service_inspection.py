"""运维检查必须识别进程存活但 Worker 心跳失效的情况。"""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from deploy.check_services import inspect_services


class ServiceInspectionTest(unittest.TestCase):
    def test_worker_health_failure_is_not_hidden_by_running_processes(self):
        processes = "\n".join(
            f"{name} RUNNING pid 1" for name in (
                "backend", "frontend", "knowledge-worker", "memory-worker", "durable-worker"
            )
        )
        for worker_status in (200, 503):
            with self.subTest(worker_status=worker_status):
                def get(url, **kwargs):
                    status = worker_status if url.endswith("/health/workers") else 200
                    return httpx.Response(status, json={"checks": {}})

                with patch("deploy.check_services.httpx.get", side_effect=get), patch(
                    "deploy.check_services.subprocess.run", return_value=SimpleNamespace(stdout=processes)
                ):
                    result = inspect_services(backend="http://backend", frontend="http://frontend")
                self.assertTrue(result["checks"]["processes"]["ok"])
                self.assertEqual(result["ok"], worker_status == 200)
