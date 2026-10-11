from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.services.tools.credentials import ToolCredentialResolver


class ToolCredentialResolverTest(unittest.TestCase):
    def test_public_env_keys_obey_shared_fallback_switch(self) -> None:
        """读取新 Key 不代表自动共享；关闭 fallback 后仍不能使用全局凭据。"""
        config = SimpleNamespace(exa_api_key=" fixture-exa-key ", context7_api_key=" fixture-context-key ")
        with patch("app.services.tools.credentials.settings", config):
            for provider, expected in (("exa", "fixture-exa-key"), ("context7", "fixture-context-key")):
                with self.subTest(provider=provider):
                    enabled = ToolCredentialResolver(allow_env_fallback=True).resolve(user_id=None, provider_key=provider)
                    self.assertEqual(enabled.api_key, expected)
                    self.assertEqual(enabled.source, "env")
                    self.assertTrue(enabled.is_enabled)
                    disabled = ToolCredentialResolver(allow_env_fallback=False).resolve(user_id=None, provider_key=provider)
                    self.assertIsNone(disabled.api_key)
                    self.assertEqual(disabled.source, "env_fallback_disabled")
                    self.assertFalse(disabled.is_enabled)

    def test_user_key_and_explicit_disable_take_precedence_over_public_env_keys(self) -> None:
        """个人凭据覆盖全局 Key，关闭服务后也不能使用全局 Key 绕过。"""
        config = SimpleNamespace(exa_api_key="fixture-env-key", context7_api_key="fixture-env-key")
        with patch("app.services.tools.credentials.settings", config):
            for provider in ("exa", "context7"):
                with self.subTest(provider=provider):
                    resolver = ToolCredentialResolver(allow_env_fallback=True)
                    resolver.repo = Mock()
                    resolver.repo.get_credential.return_value = SimpleNamespace(is_enabled=True, api_key="encrypted-fixture")
                    resolver.secrets = Mock()
                    resolver.secrets.decrypt.return_value = "fixture-user-key"
                    personal = resolver.resolve(user_id="fixture-user", provider_key=provider)
                    self.assertEqual(personal.api_key, "fixture-user-key")
                    self.assertEqual(personal.source, "user")
                    resolver.repo.get_credential.return_value.is_enabled = False
                    disabled = resolver.resolve(user_id="fixture-user", provider_key=provider)
                    self.assertEqual(disabled.source, "user_disabled")
                    self.assertIsNone(disabled.api_key)
                    self.assertFalse(disabled.is_enabled)

    def test_env_fallback_disabled_does_not_use_global_key(self) -> None:
        resolver = ToolCredentialResolver(allow_env_fallback=False)

        credential = resolver.resolve(user_id=None, provider_key="tavily")

        self.assertIsNone(credential.api_key)
        self.assertFalse(credential.is_enabled)
        self.assertIn(credential.source, {"missing", "env_fallback_disabled"})

    def test_env_fallback_enabled_can_use_global_key(self) -> None:
        resolver = ToolCredentialResolver(allow_env_fallback=True)

        credential = resolver.resolve(user_id=None, provider_key="tavily")

        if credential.api_key:
            self.assertTrue(credential.is_enabled)
            self.assertEqual(credential.source, "env")
        else:
            self.assertFalse(credential.is_enabled)
            self.assertEqual(credential.source, "missing")


if __name__ == "__main__":
    unittest.main()
