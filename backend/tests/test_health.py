import asyncio
import os
import unittest
from unittest.mock import AsyncMock, Mock, patch

import psycopg
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.health import router
from app.services.health_service import _probe, check_durable_worker, check_redis


class HealthTest(unittest.TestCase):
    @patch("app.services.health_service.Redis.from_url")
    def test_redis_probe_has_no_background_retry(self, from_url):
        check_redis()
        self.assertEqual(from_url.call_args.kwargs["retry"].get_retries(), 0)
        from_url.return_value.__enter__.return_value.ping.assert_called_once()

    def setUp(self):
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    @patch("app.services.health_service.check_redis")
    @patch("app.services.health_service.check_postgres")
    def test_ready_when_dependencies_are_available(self, postgres, redis):
        response = self.client.get("/health/ready")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ready")
        postgres.assert_called_once()
        redis.assert_called_once()
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_dependency_failure_is_not_liveness_failure_and_hides_secrets(self):
        for failing in ("check_postgres", "check_redis"):
            with self.subTest(failing=failing), patch("app.services.health_service.check_postgres"), \
                    patch("app.services.health_service.check_redis"):
                with patch(f"app.services.health_service.{failing}", side_effect=RuntimeError("secret-at-private-host")):
                    response = self.client.get("/health/ready")
                    self.assertEqual(response.status_code, 503)
                    self.assertNotIn("secret-at-private-host", response.text)
                    self.assertEqual(self.client.get("/health/live").status_code, 200)
                    self.assertEqual(self.client.get("/health").status_code, 200)

    @patch("app.services.health_service.check_durable_worker", return_value=True)
    def test_worker_ready_when_a_recent_worker_is_available(self, worker):
        response = self.client.get("/health/workers")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["checks"]["durable_worker"]["status"], "ok")
        self.assertEqual(response.headers["cache-control"], "no-store")
        worker.assert_called_once()

    @patch("app.services.health_service.check_durable_worker", return_value=False)
    @patch("app.services.health_service.check_postgres")
    @patch("app.services.health_service.check_redis")
    def test_absent_worker_fails_background_health_but_not_web_readiness(self, redis, postgres, worker):
        # 空队列也不能掩盖 Worker 停止；普通问答仍可接收请求。
        response = self.client.get("/health/workers")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["status"], "not_ready")
        self.assertEqual(self.client.get("/health/ready").status_code, 200)
        self.assertEqual(self.client.get("/health/live").status_code, 200)

    @patch("app.services.health_service.check_durable_worker", side_effect=RuntimeError("private-db-password"))
    def test_worker_probe_failure_hides_internal_errors(self, worker):
        response = self.client.get("/health/workers")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private-db-password", response.text)
        self.assertEqual(response.json()["checks"]["durable_worker"]["status"], "unavailable")

    def test_probe_timeout_returns_unavailable(self):
        with patch("app.services.health_service.asyncio.wait_for", new_callable=AsyncMock,
                   side_effect=TimeoutError), patch("app.services.health_service.asyncio.to_thread", new=Mock(return_value=None)):
            result = asyncio.run(_probe(lambda: None))
        self.assertEqual(result["status"], "unavailable")


@unittest.skipUnless(os.getenv("TEST_POSTGRES_URL"), "需要显式配置 PostgreSQL 集成测试连接")
class WorkerHealthPostgresTest(unittest.TestCase):
    def test_heartbeat_query_rejects_stopped_stale_and_missing_workers(self):
        # 临时表遮蔽同名业务表，真实查询但不写入或修改任何实际 Worker 记录。
        scenarios = (
            ([], False),
            ([("idle", 0)], True),
            ([("running", 0)], True),
            ([("stopped", 0)], False),
            ([("idle", 120)], False),
            ([("running", 120)], False),
            ([("stopped", 0), ("idle", 120), ("running", 0)], True),
        )
        for rows, expected in scenarios:
            with self.subTest(rows=rows):
                url = os.environ["TEST_POSTGRES_URL"].replace("postgresql+psycopg://", "postgresql://", 1)

                def connect():
                    connection = psycopg.connect(url, connect_timeout=2)
                    connection.execute(
                        "CREATE TEMP TABLE agent_worker_heartbeats (status text, heartbeat_at timestamptz)"
                    )
                    for status, age in rows:
                        connection.execute(
                            "INSERT INTO agent_worker_heartbeats VALUES (%s, CURRENT_TIMESTAMP - (%s * INTERVAL '1 second'))",
                            (status, age),
                        )
                    return connection

                with patch("app.services.health_service._connect_postgres", side_effect=connect):
                    self.assertIs(check_durable_worker(), expected)
