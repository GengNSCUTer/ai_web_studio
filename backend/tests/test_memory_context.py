from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.services.memory_service import MemoryService
from app.schemas.memory import MemorySuggestion


class FakeMemoryRepository:
    def __init__(self, memories: list[object]):
        self.memories = memories

    def list_by_user(self, user_id: str, *, enabled_only: bool = False) -> list[object]:
        return self.memories


class FakeConversationRepository:
    def __init__(self, conversations: dict[str, object]):
        self.conversations = conversations

    def get_by_user(self, conversation_id: str, user_id: str) -> object | None:
        return self.conversations.get(conversation_id)


class MemoryContextTest(unittest.TestCase):
    def test_conversation_sourced_project_memory_isolated_by_project(self) -> None:
        memories = [
            SimpleNamespace(
                memory_type="project",
                title="项目 A 架构",
                content="Agent 使用 PostgreSQL",
                source_conversation_id="conversation-a",
            ),
            SimpleNamespace(
                memory_type="project",
                title="项目 B 架构",
                content="Agent 使用 MongoDB",
                source_conversation_id="conversation-b",
            ),
        ]
        conversations = {
            "conversation-a": SimpleNamespace(project_id="project-a"),
            "conversation-b": SimpleNamespace(project_id="project-b"),
        }
        service = MemoryService(
            FakeMemoryRepository(memories),
            FakeConversationRepository(conversations),
        )

        context, count, _ = service.build_memory_context(
            "u1",
            max_chars=1000,
            query="Agent 项目架构",
            project_id="project-a",
        )

        self.assertIn("PostgreSQL", context or "")
        self.assertNotIn("MongoDB", context or "")
        self.assertEqual(count, 1)

    def test_memory_candidates_classify_sensitive_volatile_and_instruction_risks(self) -> None:
        suggestions = [
            MemorySuggestion(
                memory_type="fact",
                title="测试凭证",
                content="api_key: top-secret-value",
            ),
            MemorySuggestion(
                memory_type="project",
                title="临时发布",
                content="本周使用灰度环境",
            ),
            MemorySuggestion(
                memory_type="instruction",
                title="长期回答规则",
                content="每次都先输出完整推理过程",
            ),
        ]

        enriched = MemoryService.enrich_suggestion_risks(
            suggestions=suggestions,
            existing_memories=[],
        )

        self.assertEqual([item.risk_level for item in enriched], ["sensitive", "volatile", "review_required"])

    def test_duplicate_risk_takes_precedence_over_content_review(self) -> None:
        existing = SimpleNamespace(
            id="memory-existing",
            memory_type="instruction",
            title="长期回答规则",
            content="每次回答都使用中文",
        )
        suggestion = MemorySuggestion(
            memory_type="instruction",
            title="长期回答规则",
            content="每次回答都使用中文",
        )

        enriched = MemoryService.enrich_suggestion_risks(
            suggestions=[suggestion],
            existing_memories=[existing],
        )

        self.assertEqual(enriched[0].risk_level, "duplicate")

    def test_reordered_terms_are_detected_as_duplicate_candidate(self) -> None:
        existing = SimpleNamespace(
            id="memory-existing",
            memory_type="project",
            title="项目技术栈",
            content="Agent 项目使用 PostgreSQL 和 RAG 检索",
        )
        suggestion = MemorySuggestion(
            memory_type="project",
            title="项目技术栈补充",
            content="RAG 检索与 PostgreSQL 是 Agent 项目的技术基础",
        )
        enriched = MemoryService.enrich_suggestion_risks(
            suggestions=[suggestion],
            existing_memories=[existing],
        )
        self.assertEqual(enriched[0].risk_level, "duplicate")

    def test_low_confidence_candidate_requires_explicit_review(self) -> None:
        suggestion = MemorySuggestion(
            memory_type="fact",
            title="不确定偏好",
            content="用户可能偏好简短回答",
            confidence="low",
        )
        enriched = MemoryService.enrich_suggestion_risks(suggestions=[suggestion], existing_memories=[])
        self.assertEqual(enriched[0].risk_level, "review_required")

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
