from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.tools.quality import (
    decide_tool_result_action,
    evaluate_tool_result_quality,
    is_usable_tool_result,
    quality_status_for_result,
    validate_quality_contract,
)
from app.services.tools.schemas import ExternalSource, PlannedToolCall, ToolCallResult


def _source(*, display_text: str = "result", raw: object | None = None) -> ExternalSource:
    return ExternalSource(
        source_type="test",
        provider="test",
        title="Test result",
        display_text=display_text,
        metadata={"raw": raw} if raw is not None else {},
    )


class ToolResultQualityTest(unittest.TestCase):
    def test_quality_decision_matrix_is_explicit_and_bounded(self) -> None:
        self.assertEqual(
            decide_tool_result_action(status="valid").action,
            "continue",
        )
        self.assertEqual(
            decide_tool_result_action(
                status="invalid",
                retryable=True,
                retry_allowed=False,
            ).action,
            "replan",
        )
        self.assertEqual(
            decide_tool_result_action(
                status="invalid",
                retryable=False,
                fallback_available=True,
                read_only=True,
                risk_level="low",
            ).action,
            "fallback",
        )
        self.assertEqual(
            decide_tool_result_action(
                status="uncertain",
                fallback_available=False,
                read_only=True,
                risk_level="low",
            ).action,
            "replan",
        )
        self.assertEqual(
            decide_tool_result_action(
                status="uncertain",
                fallback_available=True,
                read_only=False,
                risk_level="high",
            ).action,
            "clarify",
        )

    def test_empty_result_is_invalid_by_default(self) -> None:
        quality = evaluate_tool_result_quality(sources=[])

        self.assertEqual(quality.status, "invalid")
        self.assertIn("no_sources", quality.reasons)

    def test_empty_evidence_is_invalid_even_when_contract_allows_empty(self) -> None:
        quality = evaluate_tool_result_quality(
            sources=[],
            contract={"allow_empty": True},
        )

        self.assertEqual(quality.status, "invalid")
        self.assertIn("no_sources", quality.reasons)

    def test_trusted_empty_answer_is_valid_only_when_contract_allows_empty(self) -> None:
        quality = evaluate_tool_result_quality(
            sources=[],
            contract={"allow_empty": True},
            result_semantics="empty_answer",
        )

        self.assertEqual(quality.status, "valid")
        self.assertEqual(quality.metadata["result_semantics"], "empty_answer")

        rejected = evaluate_tool_result_quality(
            sources=[],
            result_semantics="empty_answer",
        )
        self.assertEqual(rejected.status, "invalid")
        self.assertIn("empty_answer_not_allowed", rejected.reasons)

    def test_unknown_result_semantics_fails_closed(self) -> None:
        quality = evaluate_tool_result_quality(
            sources=[],
            contract={"allow_empty": True},
            result_semantics="provider_claims_everything_is_fine",
        )

        self.assertEqual(quality.status, "invalid")
        self.assertIn("unsupported_result_semantics", quality.reasons)

    def test_contract_can_require_an_reviewed_semantic_profile(self) -> None:
        quality = evaluate_tool_result_quality(
            sources=[_source(display_text="任意 JSON 文本")],
            contract={"require_semantic_profile": True},
        )

        self.assertEqual(quality.status, "invalid")
        self.assertIn("semantic_profile_required", quality.reasons)

    def test_contract_can_require_a_specific_result_semantics(self) -> None:
        contract = {
            "expected_result_semantics": "approval_draft",
            "semantic_profile": "approval_draft",
            "profile_mapping": {
                "evidence_paths": ["/sources/*/display_text"],
                "identity_paths": ["/sources/*/metadata/raw/file_id"],
            },
        }
        source = _source(display_text="可审查 Diff", raw={"file_id": "file-1"})

        wrong = evaluate_tool_result_quality(sources=[source], contract=contract)
        correct = evaluate_tool_result_quality(
            sources=[source],
            contract=contract,
            result_semantics="approval_draft",
        )

        self.assertEqual(wrong.status, "invalid")
        self.assertIn("unexpected_result_semantics", wrong.reasons)
        self.assertEqual(correct.status, "valid")

    def test_semantic_profile_reuses_the_same_contract_for_different_provider_shapes(self) -> None:
        tavily_like = _source(
            raw={
                "results": [
                    {"title": "A", "url": "https://a.example", "content": "first evidence"},
                    {"title": "B", "url": "https://b.example", "content": "second evidence"},
                ]
            }
        )
        internal_search_like = _source(
            raw={
                "items": [
                    {"name": "A", "href": "https://a.example", "text": "first evidence"},
                ]
            }
        )
        tavily_contract = {
            "semantic_profile": "web_search",
            "profile_mapping": {
                "evidence_paths": ["/sources/*/metadata/raw/results/*/content"],
                "identity_paths": ["/sources/*/metadata/raw/results/*/url"],
                "collection_paths": ["/sources/*/metadata/raw/results"],
                "item_evidence_paths": ["/content"],
                "item_identity_paths": ["/url"],
                "min_collection_items": 1,
            },
        }
        internal_contract = {
            "semantic_profile": "web_search",
            "profile_mapping": {
                "evidence_paths": ["/sources/*/metadata/raw/items/*/text"],
                "identity_paths": ["/sources/*/metadata/raw/items/*/href"],
                "collection_paths": ["/sources/*/metadata/raw/items"],
                "item_evidence_paths": ["/text"],
                "item_identity_paths": ["/href"],
            },
        }

        first = evaluate_tool_result_quality(sources=[tavily_like], contract=tavily_contract)
        second = evaluate_tool_result_quality(sources=[internal_search_like], contract=internal_contract)

        self.assertEqual(first.status, "valid")
        self.assertEqual(second.status, "valid")
        self.assertEqual(first.metadata["semantic_profile"], "web_search")
        self.assertEqual(second.metadata["semantic_profile"], "web_search")

    def test_semantic_profile_fails_closed_when_business_fields_are_missing(self) -> None:
        quality = evaluate_tool_result_quality(
            sources=[_source(raw={"results": [{"title": "only title"}]})],
            contract={
                "semantic_profile": "web_search",
                "profile_mapping": {
                    "evidence_paths": ["/sources/*/metadata/raw/results/*/content"],
                    "identity_paths": ["/sources/*/metadata/raw/results/*/url"],
                    "collection_paths": ["/sources/*/metadata/raw/results"],
                    "item_evidence_paths": ["/content"],
                    "item_identity_paths": ["/url"],
                },
            },
        )

        self.assertEqual(quality.status, "invalid")
        self.assertIn("profile_missing_evidence", quality.reasons)
        self.assertIn("profile_missing_identity", quality.reasons)
        self.assertIn("profile_item_missing_evidence", quality.reasons)

    def test_empty_collection_requires_trusted_empty_answer_semantics(self) -> None:
        contract = {
            "allow_empty": True,
            "semantic_profile": "web_search",
            "profile_mapping": {
                "evidence_paths": ["/sources/*/metadata/raw/results/*/content"],
                "identity_paths": ["/sources/*/metadata/raw/results/*/url"],
                "collection_paths": ["/sources/*/metadata/raw/results"],
                "item_evidence_paths": ["/content"],
                "item_identity_paths": ["/url"],
            },
        }
        empty_source = _source(display_text="未找到相关结果", raw={"results": []})

        invalid = evaluate_tool_result_quality(sources=[empty_source], contract=contract)
        valid = evaluate_tool_result_quality(
            sources=[empty_source], contract=contract, result_semantics="empty_answer"
        )

        self.assertEqual(invalid.status, "invalid")
        self.assertIn("profile_empty_collection", invalid.reasons)
        self.assertEqual(valid.status, "valid")

    def test_profile_mapping_is_declarative_and_bounded(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires profile_mapping"):
            validate_quality_contract({"semantic_profile": "web_search"})
        with self.assertRaisesRegex(ValueError, "identity_paths"):
            validate_quality_contract(
                {
                    "semantic_profile": "web_search",
                    "profile_mapping": {"evidence_paths": ["/sources/*/display_text"]},
                }
            )
        with self.assertRaisesRegex(ValueError, "requires collection_paths"):
            validate_quality_contract(
                {
                    "semantic_profile": "web_search",
                    "profile_mapping": {
                        "evidence_paths": ["/sources/*/display_text"],
                        "identity_paths": ["/sources/*/title"],
                    },
                }
            )

        with self.assertRaisesRegex(ValueError, "requires item_identity_paths"):
            validate_quality_contract(
                {
                    "semantic_profile": "web_search",
                    "profile_mapping": {
                        "evidence_paths": ["/sources/*/display_text"],
                        "identity_paths": ["/sources/*/title"],
                        "collection_paths": ["/sources/*/metadata/raw/items"],
                        "item_evidence_paths": ["/text"],
                    },
                }
            )

        # 文件类 Profile 允许已经由 Mapper 展平的 Source，不强行要求 raw 集合。
        normalized = validate_quality_contract(
            {
                "semantic_profile": "file_read",
                "profile_mapping": {
                    "evidence_paths": ["/sources/*/display_text"],
                    "identity_paths": ["/sources/*/metadata/file_id"],
                },
            }
        )
        self.assertEqual(normalized["semantic_profile"], "file_read")
        with self.assertRaisesRegex(ValueError, "relative to one collection item"):
            validate_quality_contract(
                {
                    "semantic_profile": "web_search",
                    "profile_mapping": {
                        "evidence_paths": ["/sources/*/display_text"],
                        "identity_paths": ["/sources/*/title"],
                        "collection_paths": ["/sources/*/metadata/raw/items"],
                        "item_evidence_paths": ["/sources/*/content"],
                        "item_identity_paths": ["/href"],
                    },
                }
            )

    def test_profile_name_is_normalized_for_stable_metadata(self) -> None:
        normalized = validate_quality_contract(
            {
                "semantic_profile": "  FILE_READ ",
                "profile_mapping": {
                    "evidence_paths": ["/sources/*/display_text"],
                    "identity_paths": ["/sources/*/metadata/file_id"],
                },
            }
        )

        self.assertEqual(normalized["semantic_profile"], "file_read")

    def test_profile_rejects_unknown_mapping_field(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported quality_contract.profile_mapping fields"):
            validate_quality_contract(
                {
                    "semantic_profile": "file_read",
                    "profile_mapping": {
                        "evidence_paths": ["/sources/*/display_text"],
                        "identity_paths": ["/sources/*/metadata/file_id"],
                        "llm_rule": "accept everything",
                    },
                }
            )

    def test_profile_checks_collection_item_shape_and_minimum(self) -> None:
        contract = {
            "semantic_profile": "web_search",
            "profile_mapping": {
                "evidence_paths": ["/sources/*/metadata/raw/items/*/text"],
                "identity_paths": ["/sources/*/metadata/raw/items/*/href"],
                "collection_paths": ["/sources/*/metadata/raw/items"],
                "item_evidence_paths": ["/text"],
                "item_identity_paths": ["/href"],
                "min_collection_items": 3,
            },
        }

        malformed = evaluate_tool_result_quality(
            sources=[_source(raw={"items": [{"text": "ok"}, "not-an-object"]})],
            contract=contract,
        )
        self.assertEqual(malformed.status, "invalid")
        self.assertIn("profile_insufficient_collection_items", malformed.reasons)
        self.assertIn("profile_item_not_object", malformed.reasons)

        valid = evaluate_tool_result_quality(
            sources=[
                _source(
                    raw={
                        "items": [
                            {"text": "first", "href": "https://one.example"},
                            {"text": "second", "href": "https://two.example"},
                            {"text": "third", "href": "https://three.example"},
                        ]
                    }
                )
            ],
            contract=contract,
        )
        self.assertEqual(valid.status, "valid")

    def test_profile_rejects_collection_overflow(self) -> None:
        contract = {
            "semantic_profile": "web_search",
            "profile_mapping": {
                "evidence_paths": ["/sources/*/metadata/raw/items/*/text"],
                "identity_paths": ["/sources/*/metadata/raw/items/*/href"],
                "collection_paths": ["/sources/*/metadata/raw/items"],
                "item_evidence_paths": ["/text"],
                "item_identity_paths": ["/href"],
            },
        }
        source = _source(
            raw={
                "items": [
                    {"text": f"evidence-{index}", "href": f"https://{index}.example"}
                    for index in range(129)
                ]
            }
        )

        quality = evaluate_tool_result_quality(sources=[source], contract=contract)

        self.assertEqual(quality.status, "invalid")
        self.assertIn("profile_collection_too_large", quality.reasons)

    def test_profile_does_not_treat_display_text_as_structured_business_fields(self) -> None:
        quality = evaluate_tool_result_quality(
            sources=[
                _source(
                    display_text='{"file_id":"fake","content":"fake"}',
                    raw={},
                )
            ],
            contract={
                "semantic_profile": "file_read",
                "profile_mapping": {
                    "evidence_paths": ["/sources/*/metadata/raw/content"],
                    "identity_paths": ["/sources/*/metadata/raw/file_id"],
                },
            },
        )

        self.assertEqual(quality.status, "invalid")
        self.assertIn("profile_missing_evidence", quality.reasons)
        self.assertIn("profile_missing_identity", quality.reasons)

    def test_profile_resolves_valid_json_pointer_escapes(self) -> None:
        """Catalog 接受的 ~0 / ~1 Profile 路径必须能在运行时读取字段。"""

        contract = {
            "semantic_profile": "file_read",
            "profile_mapping": {
                "evidence_paths": ["/sources/*/metadata/raw/content~1text"],
                "identity_paths": ["/sources/*/metadata/raw/file~0id"],
            },
        }
        quality = evaluate_tool_result_quality(
            sources=[
                _source(
                    raw={
                        "content/text": "可读取的文件内容",
                        "file~id": "file-1",
                    }
                )
            ],
            contract=contract,
        )

        self.assertEqual(quality.status, "valid")

    def test_file_read_profile_requires_file_identity(self) -> None:
        quality = evaluate_tool_result_quality(
            sources=[_source(display_text="actual content", raw={"content": "actual content"})],
            contract={
                "semantic_profile": "file_read",
                "profile_mapping": {
                    "evidence_paths": ["/sources/*/metadata/raw/content"],
                    "identity_paths": ["/sources/*/metadata/raw/file_id"],
                },
            },
        )

        self.assertEqual(quality.status, "invalid")
        self.assertIn("profile_missing_identity", quality.reasons)

    def test_profile_request_match_uses_verified_request_context(self) -> None:
        contract = {
            "semantic_profile": "weather",
            "profile_mapping": {
                "evidence_paths": ["/sources/*/metadata/raw/weather"],
                "identity_paths": ["/sources/*/metadata/raw/city"],
                "collection_paths": ["/sources"],
                "item_evidence_paths": ["/metadata/raw/weather"],
                "item_identity_paths": ["/metadata/raw/city"],
                "request_matches": [
                    {
                        "request_path": "/city",
                        "result_paths": ["/sources/*/metadata/raw/city"],
                        "normalizer": "text_loose",
                    }
                ],
            },
        }
        source = _source(raw={"city": "广州市", "weather": "晴"})

        valid = evaluate_tool_result_quality(
            sources=[source],
            contract=contract,
            request_context={"city": "广州"},
        )
        mismatch = evaluate_tool_result_quality(
            sources=[source],
            contract=contract,
            request_context={"city": "深圳"},
        )

        self.assertEqual(valid.status, "valid")
        self.assertEqual(mismatch.status, "invalid")
        self.assertIn("request_result_mismatch:0", mismatch.reasons)

    def test_request_match_contract_is_bounded(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid JSON-pointer escape"):
            validate_quality_contract(
                {
                    "semantic_profile": "file_read",
                    "profile_mapping": {
                        "evidence_paths": ["/sources/*/metadata/raw/content~2text"],
                        "identity_paths": ["/sources/*/metadata/raw/file_id"],
                    },
                }
            )

        with self.assertRaisesRegex(ValueError, "request_matches.normalizer"):
            validate_quality_contract(
                {
                    "semantic_profile": "file_read",
                    "profile_mapping": {
                        "evidence_paths": ["/sources/*/display_text"],
                        "identity_paths": ["/sources/*/metadata/file_id"],
                        "request_matches": [
                            {
                                "request_path": "/file_id",
                                "result_paths": ["/sources/*/metadata/file_id"],
                                "normalizer": "llm",
                            }
                        ],
                    },
                }
            )

        with self.assertRaisesRegex(ValueError, "request_path does not support wildcards"):
            validate_quality_contract(
                {
                    "semantic_profile": "file_read",
                    "profile_mapping": {
                        "evidence_paths": ["/sources/*/display_text"],
                        "identity_paths": ["/sources/*/metadata/file_id"],
                        "request_matches": [
                            {
                                "request_path": "/files/*/file_id",
                                "result_paths": ["/sources/*/metadata/file_id"],
                                "normalizer": "text",
                            }
                        ],
                    },
                }
            )

        for malformed_path in ("/city//name", "/city/", "/city/~2name"):
            with self.subTest(malformed_path=malformed_path):
                with self.assertRaisesRegex(ValueError, "request_path"):
                    validate_quality_contract(
                        {
                            "semantic_profile": "file_read",
                            "profile_mapping": {
                                "evidence_paths": ["/sources/*/display_text"],
                                "identity_paths": ["/sources/*/metadata/file_id"],
                                "request_matches": [
                                    {
                                        "request_path": malformed_path,
                                        "result_paths": ["/sources/*/metadata/file_id"],
                                        "normalizer": "text",
                                    }
                                ],
                            },
                        }
                    )

    def test_profile_does_not_accept_whole_object_as_business_evidence(self) -> None:
        quality = evaluate_tool_result_quality(
            sources=[_source(raw={"result": {"unrelated": "payload"}})],
            contract={
                "semantic_profile": "file_read",
                "profile_mapping": {
                    "evidence_paths": ["/sources/*/metadata/raw/result"],
                    "identity_paths": ["/sources/*/metadata/raw/result"],
                },
            },
        )

        self.assertEqual(quality.status, "invalid")
        self.assertIn("profile_missing_evidence", quality.reasons)
        self.assertIn("profile_missing_identity", quality.reasons)

    def test_required_and_non_empty_paths_are_checked(self) -> None:
        quality = evaluate_tool_result_quality(
            sources=[_source(raw={"location": ""})],
            contract={
                "required_paths": ["/sources/0/metadata/raw/location"],
                "non_empty_paths": ["/sources/0/display_text"],
            },
        )

        self.assertEqual(quality.status, "invalid")
        self.assertIn("missing_required:/sources/0/metadata/raw/location", quality.reasons)

    def test_semantically_empty_serialized_payload_is_invalid(self) -> None:
        quality = evaluate_tool_result_quality(
            sources=[_source(display_text="[]")],
            contract={"non_empty_paths": ["/sources/0/display_text"]},
        )

        self.assertEqual(quality.status, "invalid")
        self.assertIn("empty_path:/sources/0/display_text", quality.reasons)

    def test_stale_business_result_is_uncertain(self) -> None:
        stale = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        quality = evaluate_tool_result_quality(
            sources=[_source(raw={"updated_at": stale})],
            contract={
                "freshness_field": "/sources/0/metadata/raw/updated_at",
                "max_age_seconds": 60,
            },
        )

        self.assertEqual(quality.status, "uncertain")
        self.assertIn("stale_result", quality.reasons)

    def test_min_sources_is_uncertain_instead_of_invalid(self) -> None:
        quality = evaluate_tool_result_quality(
            sources=[_source()],
            contract={"min_sources": 2},
        )

        self.assertEqual(quality.status, "uncertain")
        self.assertIn("insufficient_sources", quality.reasons)

    def test_numeric_range_and_enum_are_deterministic(self) -> None:
        quality = evaluate_tool_result_quality(
            sources=[_source(raw={"confidence": "1.4", "kind": "unexpected"})],
            contract={
                "numeric_ranges": {"/sources/0/metadata/raw/confidence": {"min": 0, "max": 1}},
                "enum_paths": {"/sources/0/metadata/raw/kind": ["expected"]},
            },
        )

        self.assertEqual(quality.status, "invalid")
        self.assertIn("above_max:/sources/0/metadata/raw/confidence", quality.reasons)
        self.assertIn("enum_mismatch:/sources/0/metadata/raw/kind", quality.reasons)

    def test_invalid_contract_is_rejected_before_runtime(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported quality contract fields"):
            validate_quality_contract({"required_path": ["/sources/0/display_text"]})

        with self.assertRaisesRegex(ValueError, "min exceeds max"):
            validate_quality_contract(
                {
                    "numeric_ranges": {
                        "/sources/0/metadata/raw/value": {"min": 10, "max": 1}
                    }
                }
            )

        with self.assertRaisesRegex(ValueError, "Unsupported quality_contract.numeric_ranges fields"):
            validate_quality_contract(
                {
                    "numeric_ranges": {
                        "/sources/0/metadata/raw/value": {"min": 0, "unexpected": 1}
                    }
                }
            )

        with self.assertRaisesRegex(ValueError, "min_confidence"):
            validate_quality_contract(
                {"confidence_path": "/sources/0/metadata/raw/confidence", "min_confidence": True}
            )

    def test_freshness_contract_requires_both_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "provided together"):
            validate_quality_contract({"freshness_field": "/sources/0/metadata/raw/updated_at"})

    def test_boolean_output_is_not_a_numeric_value(self) -> None:
        quality = evaluate_tool_result_quality(
            sources=[_source(raw={"value": True})],
            contract={"numeric_ranges": {"/sources/0/metadata/raw/value": {"min": 0, "max": 1}}},
        )

        self.assertEqual(quality.status, "invalid")
        self.assertIn("not_numeric:/sources/0/metadata/raw/value", quality.reasons)

    def test_enum_matching_does_not_coerce_boolean_to_integer(self) -> None:
        quality = evaluate_tool_result_quality(
            sources=[_source(raw={"kind": True})],
            contract={"enum_paths": {"/sources/0/metadata/raw/kind": [1]}},
        )

        self.assertEqual(quality.status, "invalid")
        self.assertIn("enum_mismatch:/sources/0/metadata/raw/kind", quality.reasons)

    def test_unknown_quality_status_fails_closed_but_legacy_unknown_remains_compatible(self) -> None:
        call = PlannedToolCall(
            call_id="quality-status",
            tool_key="test.tool",
            provider="test",
            category="test",
            display_name="Test",
            confidence=1.0,
            reason="quality status",
        )
        source = _source()

        unexpected = ToolCallResult(
            call=call,
            status="success",
            sources=[source],
            elapsed_ms=1,
            quality_status="unexpected",
        )
        legacy = ToolCallResult(call=call, status="success", sources=[source], elapsed_ms=1)
        malformed = ToolCallResult(
            call=call,
            status="success",
            sources=[source],
            elapsed_ms=1,
            quality_status=None,  # type: ignore[arg-type]
        )
        pre_quality_record = SimpleNamespace(status="success", sources=[source])

        self.assertEqual(quality_status_for_result(unexpected)[0], "invalid")
        self.assertIn("unsupported_quality_status:unexpected", quality_status_for_result(unexpected)[1])
        self.assertTrue(quality_status_for_result(legacy)[0] == "unknown")
        self.assertEqual(quality_status_for_result(malformed)[0], "invalid")
        self.assertEqual(quality_status_for_result(pre_quality_record)[0], "unknown")

    def test_approval_draft_is_not_usable_as_strict_dependency_evidence(self) -> None:
        """编辑预览可展示，但不能被普通依赖、绑定或 Durable 成功路径消费。"""

        call = PlannedToolCall(
            call_id="preview",
            tool_key="workspace.files.propose_edit",
            provider="workspace",
            category="workspace_file",
            display_name="编辑预览",
            confidence=1.0,
            reason="仅生成 Diff",
        )
        source = _source(display_text="尚未写入的 Diff", raw={"file_id": "file-1", "applied": False})
        draft = ToolCallResult(
            call=call,
            status="success",
            sources=[source],
            elapsed_ms=1,
            quality_status="valid",
            result_semantics="approval_draft",
        )
        evidence = ToolCallResult(
            call=call,
            status="success",
            sources=[source],
            elapsed_ms=1,
            quality_status="valid",
            result_semantics="evidence",
        )
        empty_answer = ToolCallResult(
            call=call,
            status="success",
            sources=[],
            elapsed_ms=1,
            quality_status="valid",
            result_semantics="empty_answer",
        )

        self.assertFalse(is_usable_tool_result(draft))
        self.assertTrue(is_usable_tool_result(evidence))
        self.assertTrue(is_usable_tool_result(empty_answer))


if __name__ == "__main__":
    unittest.main()
