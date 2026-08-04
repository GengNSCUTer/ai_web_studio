from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.tools.quality import (
    decide_tool_result_action,
    evaluate_tool_result_quality,
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

    def test_allow_empty_is_explicit(self) -> None:
        quality = evaluate_tool_result_quality(
            sources=[],
            contract={"allow_empty": True},
        )

        self.assertEqual(quality.status, "valid")

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


if __name__ == "__main__":
    unittest.main()
