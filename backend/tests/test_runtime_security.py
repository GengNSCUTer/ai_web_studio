from __future__ import annotations

from dataclasses import replace
import unittest

from app.core.config import settings, validate_runtime_security_settings


class RuntimeSecuritySettingsTest(unittest.TestCase):
    def test_development_allows_default_auth_secret(self) -> None:
        validate_runtime_security_settings(
            replace(settings, app_env="development", auth_secret_key="change-this-before-production-ai-web-studio-secret")
        )

    def test_production_rejects_default_auth_secret(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "AUTH_SECRET_KEY"):
            validate_runtime_security_settings(
                replace(settings, app_env="production", auth_secret_key="change-this-to-a-long-random-string")
            )

    def test_production_accepts_independent_long_auth_secret(self) -> None:
        validate_runtime_security_settings(
            replace(settings, app_env="production", auth_secret_key="independent-production-secret-value-123456",
                    secret_encryption_key="another-independent-encryption-secret-123456",
                    cors_allowed_origins=("https://example.test",))
        )

    def test_production_requires_independent_encryption_secret(self) -> None:
        for encryption in ("", "short", "independent-production-secret-value-123456"):
            with self.subTest(encryption=encryption), self.assertRaisesRegex(RuntimeError, "SECRET_ENCRYPTION_KEY"):
                validate_runtime_security_settings(replace(
                    settings, app_env="production", auth_secret_key="independent-production-secret-value-123456",
                    secret_encryption_key=encryption,
                ))

    def test_production_rejects_wildcard_or_empty_cors(self) -> None:
        for origins in ((), ("*",)):
            with self.subTest(origins=origins), self.assertRaisesRegex(RuntimeError, "CORS_ALLOWED_ORIGINS"):
                validate_runtime_security_settings(replace(
                    settings, app_env="production", auth_secret_key="independent-production-secret-value-123456",
                    secret_encryption_key="another-independent-encryption-secret-123456", cors_allowed_origins=origins,
                ))


if __name__ == "__main__":
    unittest.main()
