from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.services.memory_service import MemoryService


class FakeMemoryRepository:
    def __init__(self, memories: list[object]):
        self.memories = memories

    def list_by_user(self, user_id: str, *, enabled_only: bool = False) -> list[object]:
        return self.memories


class MemoryContextTest(unittest.TestCase):
    def test_oversized_memory_does_not_starve_following_short_memory(self) -> None:
        repo = FakeMemoryRepository(
            [
                SimpleNamespace(memory_type="fact", title="huge", content="A" * 700),
                SimpleNamespace(memory_type="fact", title="small", content="SHOULD_FIT"),
            ]
        )

        context, count, chars = MemoryService(repo).build_memory_context("u1", max_chars=500)

        self.assertIsNotNone(context)
        self.assertIn("SHOULD_FIT", context or "")
        self.assertNotIn("A" * 100, context or "")
        self.assertEqual(count, 1)
        self.assertEqual(chars, len(context or ""))
        self.assertLessEqual(chars, 500)

    def test_existing_memory_prompt_skips_oversized_item_and_keeps_later_items(self) -> None:
        repo = FakeMemoryRepository(
            [
                SimpleNamespace(memory_type="fact", title="huge", content="A" * 5000),
                SimpleNamespace(memory_type="profile", title="style", content="concise"),
            ]
        )

        text = MemoryService(repo).build_existing_memory_text("u1", max_chars=200)

        self.assertIn("concise", text)
        self.assertNotIn("A" * 100, text)


if __name__ == "__main__":
    unittest.main()
