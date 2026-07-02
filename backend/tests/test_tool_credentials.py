from __future__ import annotations

import unittest

from app.services.tools.credentials import ToolCredentialResolver


class ToolCredentialResolverTest(unittest.TestCase):
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
