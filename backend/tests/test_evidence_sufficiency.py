from __future__ import annotations

import unittest

from app.services.evidence_sufficiency import assess_evidence_sufficiency
from app.services.tools.schemas import ExternalSource
from app.services.prompt_builder_service import ContextPromptBuilder


class EvidenceSufficiencyTest(unittest.TestCase):
    def test_file_claim_without_read_is_insufficient(self) -> None:
        result = assess_evidence_sufficiency(
            query="告诉我文件第一行标题并核对原文",
            skill_key="workspace.document-review",
            sources=[
                ExternalSource(
                    source_type="workspace_file_search",
                    provider="workspace",
                    title="readme.md",
                    display_text="搜索摘要",
                )
            ],
        )

        self.assertEqual(result.status, "insufficient")
        self.assertIn("missing_workspace_file_read", result.reasons)
        self.assertIn("不要声称已经核对原文", result.guidance)

    def test_non_empty_read_is_sufficient(self) -> None:
        result = assess_evidence_sufficiency(
            query="告诉我文件第一行标题",
            skill_key="workspace.document-review",
            sources=[
                ExternalSource(
                    source_type="workspace_file_read",
                    provider="workspace",
                    title="readme.md",
                    display_text="1: # 项目说明",
                    metadata={"line_start": 1, "line_end": 1},
                )
            ],
        )

        self.assertEqual(result.status, "sufficient")

    def test_empty_read_remains_insufficient(self) -> None:
        result = assess_evidence_sufficiency(
            query="读取文件内容",
            skill_key="workspace.document-review",
            sources=[
                ExternalSource(
                    source_type="workspace_file_read",
                    provider="workspace",
                    title="empty.md",
                    display_text="目标文件存在，但没有可读取的解析文本。",
                    metadata={"empty_reason": "parsed_text_empty"},
                )
            ],
        )

        self.assertEqual(result.status, "insufficient")
        self.assertIn("workspace_file_read_empty", result.reasons)

    def test_guidance_is_injected_as_system_constraint(self) -> None:
        result = ContextPromptBuilder().build_chat_messages(
            messages=[{"role": "user", "content": "读取文件第一行"}],
            system_prompt=None,
            memory_context=None,
            context_summary=None,
            summary_boundary_message_id=None,
            external_context=None,
            attachment_context=None,
            provider_type="openai-compatible",
            evidence_guidance="证据不足：不能声称已经核对原文。",
        )

        self.assertIn("当前回答证据约束", result.messages[0]["content"])
        self.assertIn("不能声称已经核对原文", result.messages[0]["content"])
        self.assertEqual(result.diagnostics["prompt_evidence_guidance_injected"], 1)


if __name__ == "__main__":
    unittest.main()
