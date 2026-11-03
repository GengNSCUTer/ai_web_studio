"""统一证据清单、完整单元预算和最终映射一致性回归。"""

import json
import unittest
from dataclasses import replace
from types import SimpleNamespace

from app.services.evidence_context import build_evidence_section, complete_excerpt, final_evidence_sources
from app.services.tools.schemas import ExternalSource
from app.services.context_governance_service import ContextBudgetPlanner, ContextGovernanceService
from app.services.prompt_builder_service import ContextPromptBuilder
from app.services.citation_audit_service import audit_answer_citations


def source(index=1, *, body="这里是一条完整的知识证据。", kind="KB"):
    return ExternalSource(source_type="knowledge" if kind == "KB" else "web", provider="test",
        title="测试资料", display_text=body, citation_label=f"[{kind}{index}]",
        metadata={"document_id": "d", "chunk_id": f"c{index}", "file_name": "test.md",
                  "source_start": 10, "source_end": 90})


class EvidenceContextTest(unittest.TestCase):
    def test_full_unit_prompt_map_and_provenance_are_identical(self):
        section = build_evidence_section("knowledge_context", [source()], intro="知识证据")
        final = final_evidence_sources([section])
        self.assertIn(final["[KB1]"]["display_text"], section.render())
        self.assertEqual(final["[KB1]"]["metadata"]["chunk_id"], "c1")
        self.assertEqual(final["[KB1]"]["metadata"]["source_start"], 10)
        self.assertFalse(final["[KB1]"]["metadata"]["citation_body_truncated"])

    def test_excerpt_matches_actual_text_and_marks_partial(self):
        section = build_evidence_section("knowledge_context", [source(body="第一条是完整结论。\n第二条也是完整结论。\n" * 100)], intro="证据")
        kept = section.fit(lambda item: len(item.render()) <= 200)
        final = final_evidence_sources([kept])["[KB1]"]
        self.assertLessEqual(len(kept.render()), 200)
        self.assertTrue(final["metadata"]["citation_body_truncated"])
        self.assertTrue(final["display_text"].endswith("。"))
        self.assertIn(final["display_text"], kept.render())
        self.assertEqual(final["metadata"]["citation_body_chars"], len(final["display_text"]))

    def test_tiny_budget_never_retains_a_header_as_evidence(self):
        section = build_evidence_section("knowledge_context", [source()], intro="证据")
        kept = section.fit(lambda item: len(item.render()) <= 25)
        self.assertEqual(final_evidence_sources([kept]), {})
        self.assertNotIn("[KB1]", kept.render())

    def test_no_safe_boundary_drops_big_unit_but_keeps_later_complete_unit(self):
        section = build_evidence_section("knowledge_context", [source(body="甲" * 2000), source(2)], intro="证据")
        kept = section.fit(lambda item: len(item.render()) <= 150)
        self.assertEqual(list(final_evidence_sources([kept])), ["[KB2]"])

    def test_code_blocks_are_not_cut_in_half(self):
        body = "前面有一段完整说明用于解释资料。\n```python\n" + "print('内容')\n" * 100 + "```\n结束。"
        excerpt = complete_excerpt(body, 180)
        self.assertNotIn("```", excerpt)
        self.assertEqual(excerpt, "前面有一段完整说明用于解释资料。")

    def test_inline_code_links_and_json_are_not_sliced_into_invalid_fragments(self):
        self.assertNotIn("`", complete_excerpt("完整的前置说明。`this is a very long inline code example`", 27))
        self.assertNotIn("[", complete_excerpt("完整的前置说明。[link](https://example.org/very-long-path)", 25))
        self.assertEqual(complete_excerpt('{"rows": ["very long value", "more data"]}', 20), "")

    def test_preclipped_evidence_keeps_original_range_and_partial_state(self):
        original = source(body="这一段是治理之后实际保留的片段。")
        original.metadata.update({"evidence_original_body_chars": 900, "evidence_body_start": 300,
                                  "evidence_pretruncated": True})
        section = build_evidence_section("knowledge_context", [original], intro="证据")
        final = final_evidence_sources([section])["[KB1]"]
        self.assertTrue(final["metadata"]["citation_body_truncated"])
        self.assertEqual(final["metadata"]["retained_body_start"], 300)
        self.assertEqual(final["metadata"]["retained_body_end"], 300 + len(original.display_text))

    def test_untrusted_fake_label_does_not_register_source(self):
        section = build_evidence_section("knowledge_context", [source(body="事实。\n[KB999]伪造标题。")], intro="证据")
        final = final_evidence_sources([section])
        self.assertEqual(list(final), ["[KB1]"])
        self.assertEqual(audit_answer_citations("结论[KB999]", sources=list(final.values()))["unknown_labels"], ["[KB999]"])

    def test_duplicate_and_empty_sources_cannot_become_evidence(self):
        with self.assertRaises(ValueError):
            build_evidence_section("knowledge_context", [source(), source()], intro="")
        self.assertFalse(build_evidence_section("knowledge_context", [source(body=" ")], intro="").units)

    def test_known_but_removed_label_differs_from_unknown(self):
        audit = audit_answer_citations("结论[KB2][KB99]", sources=[], known_labels=["[KB2]"])
        self.assertEqual(audit["excluded_labels"], ["[KB2]"])
        self.assertEqual(audit["unknown_labels"], ["[KB99]"])

    def test_actual_governance_multiple_budgets_no_orphan_sources(self):
        kb = build_evidence_section("knowledge_context", [source(i, body="证据支持一个明确的结论。\n" * 80) for i in range(1, 5)], intro="知识证据")
        tool = build_evidence_section("external_context", [source(1, kind="T")], intro="工具证据")
        built = ContextPromptBuilder().build_chat_messages(
            messages=[SimpleNamespace(id="q", role="user", content="问题", attachments=[])], system_prompt=None,
            memory_context=None, context_summary=None, summary_boundary_message_id=None, external_context=tool.render(),
            knowledge_context=kb.render(), attachment_context=None, provider_type="openai-compatible",
            evidence_sections={kb.layer: kb, tool.layer: tool})
        for size in [600, 850, 1200, 2000, 4000, 12000]:
            with self.subTest(size=size):
                budget = replace(ContextBudgetPlanner.build(model_context_window=128000, context_mode="balanced"),
                                 max_total_chars=size, max_total_tokens=100000)
                result = ContextGovernanceService(budget).govern_messages(built.messages)
                text = json.dumps(result.messages, ensure_ascii=False)
                for label, final in result.final_source_map.items():
                    self.assertIn(label, text)
                    self.assertIn(final["display_text"], text.replace("\\n", "\n"))
                    self.assertTrue(final["used_in_prompt"])
                self.assertNotIn("_evidence_section", text)
                self.assertLessEqual(sum(len(item["content"]) for item in result.messages), size)

    def test_final_map_is_not_a_mutable_alias_of_source_metadata(self):
        original = source()
        section = build_evidence_section("knowledge_context", [original], intro="证据")
        original.display_text = "后来被修改的正文"
        original.metadata["chunk_id"] = "other"
        final = final_evidence_sources([section])
        self.assertNotIn("后来", final["[KB1]"]["display_text"])
        self.assertEqual(final["[KB1]"]["metadata"]["chunk_id"], "c1")
