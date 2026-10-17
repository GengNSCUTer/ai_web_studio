import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes.health import router
from app.services.health_service import check_redis


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
