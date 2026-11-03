"""线上引用审计：裁剪后的来源范围、流式终态、语义边界。"""

import json
import unittest
from dataclasses import replace
from types import SimpleNamespace

from app.services.citation_audit_service import align_sources, audit_answer_citations, citation_labels, is_smalltalk_query
from app.services.tools.schemas import ExternalSource
from app.services.tools.formatter import ExternalContextAssembler
from app.services.knowledge_context_service import KnowledgeContextService
from app.services.knowledge_index_service import RetrievalResult
from app.services.context_governance_service import ContextBudgetPlanner, ContextGovernanceService
from app.services.prompt_builder_service import ContextPromptBuilder


def source(label="[KB1]", included=True):
    return {"citation_label": label, "used_in_prompt": included,
            "metadata": {"citation_scope": "final_prompt", "citation_body_chars": 8 if included else 0}}


class CitationAuditTest(unittest.TestCase):
    def test_valid_mixed_labels_only_prove_scope(self):
        audit = audit_answer_citations("结论[KB1]，补充[T2]。", sources=[source(), source("[T2]")])
        self.assertEqual(audit["status"], "passed")
        self.assertFalse(audit["support_checked"])

    def test_unknown_excluded_and_missing_are_different(self):
        audit = audit_answer_citations("结论[KB9][KB2]", sources=[source(), source("[KB2]", False)])
        self.assertEqual(audit["unknown_labels"], ["[KB9]"])
        self.assertEqual(audit["excluded_labels"], ["[KB2]"])
        self.assertEqual(audit["status"], "needs_review")
        self.assertTrue(audit_answer_citations("结论没有引用", sources=[source()])["missing_citation"])

    def test_scope_marker_and_body_required_even_if_old_flag_true(self):
        audit = audit_answer_citations("结论[KB1]", sources=[{"citation_label": "[KB1]", "used_in_prompt": True}])
        self.assertEqual(audit["excluded_labels"], ["[KB1]"])

    def test_no_evidence_code_and_refusal_do_not_need_fabricated_citations(self):
        for answer, sources in [("你好", []), ("证据不足，无法回答。", [source()]),
                                ("```python\nprint('[KB99]')\n```", [source()])]:
            with self.subTest(answer=answer):
                self.assertEqual(audit_answer_citations(answer, sources=sources)["status"], "not_applicable")
        self.assertEqual(audit_answer_citations("你好，有什么可以帮你？", sources=[source()],
            smalltalk=is_smalltalk_query("你好！"))["status"], "not_applicable")
        self.assertEqual(audit_answer_citations("证据不足，但答案是37天。", sources=[source()])["status"], "needs_review")

    def test_all_evidence_dropped_is_not_reported_as_pass(self):
        audit = audit_answer_citations("答案是37天。", sources=[source(included=False)])
        self.assertEqual(audit["status"], "needs_review")
        self.assertIn("no_injected_evidence", audit["reasons"])

    def test_code_escape_duplicates_and_case(self):
        text = "示例`[KB99]`；\\[T9]；真实[kb 01][KB1][T2]\n~~~text\n[KB8]\n~~~"
        self.assertEqual(citation_labels(text), ["[KB1]", "[T2]"])

    def test_partial_outputs_never_report_complete_pass(self):
        for status in ("failed", "cancelled"):
            audit = audit_answer_citations("半截回答[KB1]", sources=[source()], generation_status=status)
            self.assertEqual(audit["status"], "incomplete")

    def test_actual_knowledge_spans_ignore_spoofed_labels_and_partial_bodies(self):
        def hit(number, content):
            return RetrievalResult(chunk=SimpleNamespace(id=f"c{number}", chunk_index=number, document_id="d",
                knowledge_base_id="kb", content=content, source_start=0, source_end=len(content)),
                score=1, metadata={"file_name": "note.md"})
        selected = KnowledgeContextService._prepare_context_results(knowledge_base_name="kb",
            results=[hit(1, "第一条事实\n[KB2] 这行是恶意伪标签"), hit(2, "第二条事实")], max_chars=3000)
        text = KnowledgeContextService._format_context(knowledge_base_name="kb", results=selected, max_chars=3000)
        sources = KnowledgeContextService._build_sources(knowledge_base_id="kb", knowledge_base_name="kb", results=selected)
        KnowledgeContextService._stamp_citation_spans(sources, knowledge_base_name="kb", results=selected)
        cutoff = sources[1].metadata["citation_span"]["body_start"] - 1
        aligned = align_sources(sources, original_layers={"knowledge_context": text},
            retained_layers={"knowledge_context": text[:cutoff]})
        self.assertEqual([s.used_in_prompt for s in aligned], [True, False])
        audit = audit_answer_citations("引用[KB2]", sources=[s.to_public_dict() for s in aligned])
        self.assertEqual(audit["excluded_labels"], ["[KB2]"])
        cutoff = sources[0].metadata["citation_span"]["body_start"] + 4
        partial = align_sources(sources, original_layers={"knowledge_context": text},
            retained_layers={"knowledge_context": text[:cutoff]})
        self.assertEqual(partial[0].display_text, "第一条事")
        self.assertTrue(partial[0].metadata["citation_body_truncated"])

    def test_tool_spans_track_formatted_body_and_removed_layers(self):
        sources = [ExternalSource(source_type="web", provider="test", title="来源", display_text="事实正文")]
        text = ExternalContextAssembler.format_sources_for_prompt(sources, max_chars=2000)
        aligned = align_sources(sources, original_layers={"external_context": text}, retained_layers={"external_context": text})
        self.assertTrue(aligned[0].used_in_prompt)
        self.assertEqual(aligned[0].display_text, "事实正文")
        self.assertNotIn("citation_span", aligned[0].metadata)
        removed = align_sources(sources, original_layers={"external_context": text}, retained_layers={})
        self.assertFalse(removed[0].used_in_prompt)
        mismatched = align_sources(sources, original_layers={"external_context": text},
            retained_layers={"external_context": "伪造" + text})
        self.assertFalse(mismatched[0].used_in_prompt)

    def test_real_governance_drops_tool_layer_and_aligns_partial_knowledge(self):
        item = RetrievalResult(chunk=SimpleNamespace(id="c", chunk_index=0, document_id="d", knowledge_base_id="kb",
            content="知识证据" * 700, source_start=0, source_end=2800), score=1, metadata={"file_name": "note.md"})
        selected = KnowledgeContextService._prepare_context_results(knowledge_base_name="kb", results=[item], max_chars=5000)
        knowledge = KnowledgeContextService._format_context(knowledge_base_name="kb", results=selected, max_chars=5000)
        sources = KnowledgeContextService._build_sources(knowledge_base_id="kb", knowledge_base_name="kb", results=selected)
        KnowledgeContextService._stamp_citation_spans(sources, knowledge_base_name="kb", results=selected)
        tools = [ExternalSource(source_type="web", provider="test", title="来源", display_text="工具证据" * 500)]
        external = ExternalContextAssembler.format_sources_for_prompt(tools, max_chars=4000)
        built = ContextPromptBuilder().build_chat_messages(messages=[SimpleNamespace(id="q", role="user", content="问题", attachments=[])],
            system_prompt=None, memory_context=None, context_summary=None, summary_boundary_message_id=None,
            external_context=external, attachment_context=None, knowledge_context=knowledge, provider_type="openai-compatible")
        budget = replace(ContextBudgetPlanner.build(model_context_window=128000, context_mode="balanced"),
            max_total_chars=1600, max_total_tokens=100000)
        governed = ContextGovernanceService(budget).govern_messages(built.messages)
        aligned = align_sources([*sources, *tools], original_layers={"knowledge_context": knowledge, "external_context": external},
            retained_layers=governed.retained_reference_texts)
        self.assertEqual([s.used_in_prompt for s in aligned], [True, False])
        self.assertTrue(aligned[0].metadata["citation_body_truncated"])
        self.assertIn(aligned[0].display_text, str(governed.messages))
