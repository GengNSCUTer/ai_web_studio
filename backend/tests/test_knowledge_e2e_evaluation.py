from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.services.knowledge_e2e_evaluation_service import (
    KnowledgeE2EEvaluationConfig,
    KnowledgeE2EEvaluationService,
    audit_citations,
    build_answer_messages,
    extract_citation_labels,
    normalize_score,
    parse_generated_answer,
    parse_judge_scores,
)


class KnowledgeE2EEvaluationHelpersTest(unittest.TestCase):
    def test_extract_citation_labels_is_ordered_and_deduplicated(self) -> None:
        self.assertEqual(
            extract_citation_labels("结论 [KB2]，补充 [source-1]，再次 [KB2]，以及 [S3]"),
            ["KB2", "KB1", "KB3"],
        )

    def test_parse_generated_answer_accepts_fenced_json_and_inline_citations(self) -> None:
        parsed = parse_generated_answer(
            '```json\n{"answer":"结论 [KB1]，细节见 [KB2]","citations":["KB2"]}\n```'
        )
        self.assertEqual(parsed["answer"], "结论 [KB1]，细节见 [KB2]")
        self.assertEqual(parsed["citations"], ["KB2", "KB1"])

    def test_parse_generated_answer_falls_back_to_plain_text(self) -> None:
        parsed = parse_generated_answer("无法严格输出 JSON，但结论见 [KB1]")
        self.assertEqual(parsed["answer"], "无法严格输出 JSON，但结论见 [KB1]")
        self.assertEqual(parsed["citations"], ["KB1"])

    def test_parse_judge_scores_accepts_percentage_values(self) -> None:
        parsed = parse_judge_scores(
            '{"faithfulness": 80, "answer_relevance": 0.7, "citation_support": 0.9}'
        )
        self.assertEqual(parsed["status"], "scored")
        self.assertEqual(parsed["answer_faithfulness"], 0.8)
        self.assertEqual(parsed["answer_relevancy"], 0.7)
        self.assertEqual(parsed["citation_correctness"], 0.9)

    def test_parse_judge_scores_rejects_missing_metric(self) -> None:
        parsed = parse_judge_scores('{"faithfulness": 0.8}')
        self.assertEqual(parsed["status"], "invalid_response")
        self.assertIn("answer_relevancy", parsed["missing_fields"])
        self.assertIsNone(normalize_score(101))

    def test_audit_citations_separates_validity_from_ground_truth_precision(self) -> None:
        audit = audit_citations(
            citations=["KB1", "KB2", "KB9"],
            source_map={
                "KB1": {"chunk_id": "target-1"},
                "KB2": {"chunk_id": "distractor-2"},
            },
            ground_truth_chunk_ids=["target-1", "target-3"],
        )
        self.assertEqual(audit["citation_count"], 3)
        self.assertEqual(audit["valid_citation_count"], 2)
        self.assertEqual(audit["valid_citation_rate"], 2 / 3)
        self.assertEqual(audit["ground_truth_citation_precision"], 0.5)
        self.assertEqual(audit["ground_truth_citation_recall"], 0.5)
        self.assertEqual(audit["invalid_citations"], ["KB9"])

    def test_answer_prompt_marks_context_as_untrusted_and_contains_labels(self) -> None:
        messages = build_answer_messages(
            query="问题",
            sources=[
                {
                    "label": "KB1",
                    "file_name": "paper.pdf",
                    "chunk_index": 3,
                    "content": "证据",
                }
            ],
        )
        prompt = "\n".join(message["content"] for message in messages)
        self.assertIn("不可信", prompt)
        self.assertIn("[KB1]", prompt)


class KnowledgeE2EEvaluationServiceTest(unittest.TestCase):
    def test_serialize_sources_respects_total_context_budget(self) -> None:
        item = SimpleNamespace(
            chunk=SimpleNamespace(id="chunk-1", document_id="doc-1", chunk_index=0, content="x" * 100),
            metadata={"file_name": "a.pdf"},
            score=0.5,
            rerank_score=None,
        )
        sources, source_map = KnowledgeE2EEvaluationService._serialize_sources(
            retrieved=[item, item],
            config=KnowledgeE2EEvaluationConfig(max_context_chars=120, max_chunk_chars=80),
        )
        self.assertEqual(len(sources), 2)
        self.assertEqual(set(source_map), {"KB1", "KB2"})
        self.assertLessEqual(sum(len(source["content"]) for source in sources), 120)

    def test_run_scores_answer_and_judge_without_persisting_production_state(self) -> None:
        class FakeProvider:
            def __init__(self) -> None:
                self.calls = 0

            async def complete_chat(self, **_: object) -> str:
                self.calls += 1
                if self.calls % 2:
                    return '{"answer":"结论 [KB1]","citations":["KB1"]}'
                return (
                    '{"answer_faithfulness":0.8,"answer_relevancy":0.9,'
                    '"citation_correctness":1.0,"explanation":"supported"}'
                )

        class FakePipeline:
            def retrieve(self, **_: object) -> list[object]:
                return [
                    SimpleNamespace(
                        chunk=SimpleNamespace(
                            id="target-1",
                            document_id="doc-1",
                            chunk_index=0,
                            content="证据正文",
                        ),
                        metadata={"file_name": "paper.pdf"},
                        score=0.9,
                        rerank_score=0.8,
                    )
                ]

        case = SimpleNamespace(
            id="case-1",
            query="问题",
            expected_chunk_id="target-1",
            expected_chunk_ids_json='["target-1"]',
            expected_answer_keywords_json='["证据"]',
        )
        service = KnowledgeE2EEvaluationService(
            retrieval_pipeline=FakePipeline(),  # type: ignore[arg-type]
            chat_provider=FakeProvider(),
        )
        payload = service.run(
            user_id="user-1",
            knowledge_base=SimpleNamespace(),
            cases=[case],
            provider_type="openai-compatible",
            base_url="http://provider.test/v1",
            api_key="test-only",
            model_name="answer-model",
        )
        self.assertEqual(payload["metrics"]["scored_case_count"], 1)
        self.assertEqual(payload["metrics"]["answer_faithfulness"], 0.8)
        self.assertEqual(payload["metrics"]["answer_relevancy"], 0.9)
        self.assertEqual(payload["metrics"]["citation_correctness"], 1.0)
        self.assertEqual(payload["cases"][0]["citation_audit"]["ground_truth_citation_recall"], 1.0)


if __name__ == "__main__":
    unittest.main()
