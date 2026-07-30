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
    def test_query_selection_keeps_preferences_but_drops_irrelevant_facts(self) -> None:
        repo = FakeMemoryRepository(
            [
                SimpleNamespace(memory_type="profile", title="表达风格", content="回答简洁、有条理"),
                SimpleNamespace(memory_type="fact", title="旧旅行", content="去年去过冰岛看极光"),
                SimpleNamespace(memory_type="project", title="Agent 项目", content="项目采用 RAG 检索和工具调用"),
            ]
        )

        context, count, _ = MemoryService(repo).build_memory_context(
            "u1",
            max_chars=1000,
            query="Agent 项目的 RAG 检索如何实现？",
        )

        self.assertIn("回答简洁", context or "")
        self.assertIn("工具调用", context or "")
        self.assertNotIn("冰岛", context or "")
        self.assertEqual(count, 2)

    def test_query_selection_does_not_drop_all_facts_when_query_is_unavailable(self) -> None:
        repo = FakeMemoryRepository(
            [
                SimpleNamespace(memory_type="fact", title="first", content="ONE"),
                SimpleNamespace(memory_type="fact", title="second", content="TWO"),
            ]
        )

        context, count, _ = MemoryService(repo).build_memory_context("u1", max_chars=500)

        self.assertIn("ONE", context or "")
        self.assertIn("TWO", context or "")
        self.assertEqual(count, 2)

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
