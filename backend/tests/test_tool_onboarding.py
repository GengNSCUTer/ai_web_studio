from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from app.services.tools.onboarding import (
    ONBOARDING_CONTRACT_VERSION,
    ONBOARDING_FIXTURE_FORMAT,
    ToolOnboardingContractError,
    evaluate_tool_onboarding_fixture_bundle,
    fixture_bundle_digest,
    validate_tool_onboarding_contract,
    validate_tool_onboarding_fixture_bundle,
)
from app.services.tools.result_mappers import map_declared_mcp_result


def _valid_contract() -> dict:
    return {
        "version": ONBOARDING_CONTRACT_VERSION,
        "tool": {
            "tool_key": "mcp.demo.search.1234567890",
            "provider": "demo_search",
            "category": "web_search",
            "adapter_type": "mcp_http",
            "source_type": "mcp_server",
            "risk_level": "low",
            "read_only": True,
        },
        "quality_contract": {
            "min_sources": 1,
            "semantic_profile": "web_search",
            "profile_mapping": {
                "evidence_paths": ["/sources/*/metadata/raw/content"],
                "identity_paths": ["/sources/*/metadata/raw/url"],
                "collection_paths": ["/sources"],
                "item_evidence_paths": ["/metadata/raw/content"],
                "item_identity_paths": ["/metadata/raw/url"],
            },
        },
        "canonical_mapper": {
            "type": "collection",
            "collection_path": "/items",
            "display_text_path": "/snippet",
            "title_path": "/title",
            "url_path": "/href",
            "score_path": "/score",
            "canonical_fields": {
                "content": "/snippet",
                "url": "/href",
                "title": "/title",
            },
            "max_items": 8,
            "max_chars": 1200,
        },
        "fixture_manifest": {
            "format": ONBOARDING_FIXTURE_FORMAT,
            "bundle_digest": "a" * 64,
            "case_ids": ["success", "malformed", "request_mismatch"],
        },
    }


def _valid_fixture_bundle(*, include_request_mismatch: bool = False) -> dict:
    cases = [
        {
            "id": "success",
            "response": {
                "result": {
                    "structuredContent": {
                        "items": [
                            {
                                "snippet": "可引用的正文 API_KEY=remote-secret-value",
                                "title": "Expected",
                                "href": "https://example.test/evidence",
                                "score": 0.91,
                            }
                        ]
                    }
                }
            },
            "expected": {"quality_status": "valid", "source_count": {"min": 1, "max": 1}},
            "request_context": {"query": "Expected"},
        },
        {
            "id": "malformed",
            "response": {"result": {"structuredContent": {"items": [{"title": "缺少正文"}]}}},
            "expected": {"quality_status": "invalid", "source_count": {"min": 0, "max": 0}},
        },
    ]
    if include_request_mismatch:
        cases.append(
            {
                "id": "request_mismatch",
                "response": {
                    "result": {
                        "structuredContent": {
                            "items": [
                                {
                                    "snippet": "结构完整但不属于本次请求",
                                    "title": "Unexpected",
                                    "href": "https://example.test/other",
                                }
                            ]
                        }
                    }
                },
                "expected": {"quality_status": "invalid", "source_count": {"min": 1, "max": 1}},
                "request_context": {"query": "Expected"},
            }
        )
    return {"format": ONBOARDING_FIXTURE_FORMAT, "cases": cases}


def _contract_for_fixture(bundle: dict, *, request_match: bool = False) -> dict:
    contract = _valid_contract()
    contract["fixture_manifest"]["bundle_digest"] = fixture_bundle_digest(bundle)
    contract["fixture_manifest"]["case_ids"] = [case["id"] for case in bundle["cases"]]
    if request_match:
        contract["quality_contract"]["profile_mapping"]["request_matches"] = [
            {
                "request_path": "/query",
                "result_paths": ["/sources/*/metadata/raw/title"],
                "normalizer": "text",
            }
        ]
    return contract


class ToolOnboardingContractTest(unittest.TestCase):
    def test_accepts_a_complete_low_risk_read_only_contract(self) -> None:
        contract = validate_tool_onboarding_contract(_valid_contract())

        self.assertEqual(contract.tool["tool_key"], "mcp.demo.search.1234567890")
        self.assertEqual(contract.quality_contract["semantic_profile"], "web_search")
        self.assertEqual(contract.canonical_mapper["type"], "collection")
        self.assertEqual(contract.fixture_manifest["case_ids"][0], "success")
        self.assertEqual(len(contract.digest), 64)

    def test_contract_digest_is_stable_for_equivalent_input(self) -> None:
        first = validate_tool_onboarding_contract(_valid_contract())
        second = validate_tool_onboarding_contract(copy.deepcopy(_valid_contract()))

        self.assertEqual(first.digest, second.digest)

    def test_rejects_non_read_only_or_high_risk_tool(self) -> None:
        for field_name, value in (("read_only", False), ("risk_level", "high")):
            with self.subTest(field_name=field_name):
                payload = _valid_contract()
                payload["tool"][field_name] = value

                with self.assertRaisesRegex(ToolOnboardingContractError, "read_only|low or medium"):
                    validate_tool_onboarding_contract(payload)

    def test_rejects_non_mcp_identity(self) -> None:
        payload = _valid_contract()
        payload["tool"]["adapter_type"] = "workspace_file"

        with self.assertRaisesRegex(ToolOnboardingContractError, "adapter_type=mcp_http"):
            validate_tool_onboarding_contract(payload)

    def test_rejects_remote_empty_answer_configuration(self) -> None:
        payload = _valid_contract()
        payload["quality_contract"]["allow_empty"] = True

        with self.assertRaisesRegex(ToolOnboardingContractError, "allow_empty"):
            validate_tool_onboarding_contract(payload)

    def test_rejects_dynamic_mapper_or_unbounded_pointer(self) -> None:
        payload = _valid_contract()
        payload["canonical_mapper"]["expression"] = "return item.content"
        with self.assertRaisesRegex(ToolOnboardingContractError, "unsupported fields"):
            validate_tool_onboarding_contract(payload)

        payload = _valid_contract()
        payload["canonical_mapper"]["display_text_path"] = "/items/*/snippet"
        with self.assertRaisesRegex(ToolOnboardingContractError, "wildcards"):
            validate_tool_onboarding_contract(payload)

        payload = _valid_contract()
        payload["canonical_mapper"]["display_text_path"] = "/items/"
        with self.assertRaisesRegex(ToolOnboardingContractError, "invalid path"):
            validate_tool_onboarding_contract(payload)

    def test_rejects_reserved_canonical_fields_and_invalid_fixture_summary(self) -> None:
        payload = _valid_contract()
        payload["canonical_mapper"]["canonical_fields"] = {"result_semantics": "/state"}
        with self.assertRaisesRegex(ToolOnboardingContractError, "reserved"):
            validate_tool_onboarding_contract(payload)

        payload = _valid_contract()
        payload["fixture_manifest"]["bundle_digest"] = "not-a-digest"
        with self.assertRaisesRegex(ToolOnboardingContractError, "SHA-256"):
            validate_tool_onboarding_contract(payload)

        payload = _valid_contract()
        payload["fixture_manifest"]["case_ids"] = ["success"]
        with self.assertRaisesRegex(ToolOnboardingContractError, "missing required cases"):
            validate_tool_onboarding_contract(payload)

    def test_rejects_unknown_contract_fields_and_missing_quality_profile(self) -> None:
        payload = _valid_contract()
        payload["unsafe_override"] = True
        with self.assertRaisesRegex(ToolOnboardingContractError, "unsupported fields"):
            validate_tool_onboarding_contract(payload)

        payload = _valid_contract()
        payload["quality_contract"].pop("semantic_profile")
        payload["quality_contract"].pop("profile_mapping")
        with self.assertRaisesRegex(ToolOnboardingContractError, "semantic_profile"):
            validate_tool_onboarding_contract(payload)

    def test_declared_collection_mapper_projects_only_bounded_canonical_fields(self) -> None:
        mapper = _valid_contract()["canonical_mapper"]
        sources = map_declared_mcp_result(
            canonical_mapper=mapper,
            provider="demo_search",
            category="web_search",
            display_name="Demo Search",
            raw=_valid_fixture_bundle()["cases"][0]["response"],
        )

        self.assertEqual(len(sources), 1)
        self.assertEqual(set(sources[0].metadata["raw"]), {"content", "url", "title"})
        self.assertEqual(sources[0].url, "https://example.test/evidence")
        self.assertNotIn("remote-secret-value", sources[0].display_text)
        self.assertNotIn("remote-secret-value", sources[0].metadata["raw"]["content"])

    def test_declared_object_mapper_supports_pointer_escaping_and_fails_closed(self) -> None:
        mapper = {
            "type": "object",
            "object_path": "/payload",
            "display_text_path": "/text~1plain",
            "canonical_fields": {"content": "/text~1plain", "identity": "/id"},
            "max_chars": 32,
        }
        sources = map_declared_mcp_result(
            canonical_mapper=mapper,
            provider="demo",
            category="artifact_read",
            display_name="Demo",
            raw={"result": {"structuredContent": {"payload": {"text/plain": "正文", "id": "a-1"}}}},
        )
        self.assertEqual(sources[0].display_text, "正文")
        self.assertEqual(sources[0].metadata["raw"], {"content": "正文", "identity": "a-1"})

        missing = map_declared_mcp_result(
            canonical_mapper=mapper,
            provider="demo",
            category="artifact_read",
            display_name="Demo",
            raw={"result": {"structuredContent": {"payload": {"id": "a-1"}}}},
        )
        self.assertEqual(missing, [])

    def test_fixture_bundle_runs_mapper_and_quality_gate_without_returning_response_body(self) -> None:
        fixture = _valid_fixture_bundle(include_request_mismatch=True)
        contract = validate_tool_onboarding_contract(
            _contract_for_fixture(fixture, request_match=True)
        )
        bundle = validate_tool_onboarding_fixture_bundle(fixture, contract=contract)

        report = evaluate_tool_onboarding_fixture_bundle(contract=contract, bundle=bundle)

        self.assertTrue(report.valid)
        public = report.to_public_dict()
        self.assertNotIn("response", json.dumps(public, ensure_ascii=False))
        self.assertNotIn("remote-secret-value", json.dumps(public, ensure_ascii=False))
        self.assertEqual([case["actual_quality_status"] for case in public["cases"]], ["valid", "invalid", "invalid"])

    def test_fixture_bundle_rejects_digest_change_and_missing_case(self) -> None:
        fixture = _valid_fixture_bundle()
        contract = validate_tool_onboarding_contract(_contract_for_fixture(fixture))

        changed = copy.deepcopy(fixture)
        changed["cases"][0]["response"]["result"]["structuredContent"]["items"][0]["snippet"] = "被替换"
        with self.assertRaisesRegex(ToolOnboardingContractError, "digest"):
            validate_tool_onboarding_fixture_bundle(changed, contract=contract)

        missing_case = {"format": ONBOARDING_FIXTURE_FORMAT, "cases": fixture["cases"][:1]}
        with self.assertRaisesRegex(ToolOnboardingContractError, "case ids"):
            validate_tool_onboarding_fixture_bundle(missing_case, contract=contract)

    def test_offline_validator_reports_only_sanitized_fixture_summary(self) -> None:
        fixture = _valid_fixture_bundle()
        contract = _contract_for_fixture(fixture)
        script = Path(__file__).resolve().parents[1] / "scripts" / "validate_tool_onboarding.py"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contract_path = root / "contract.json"
            fixture_path = root / "fixture.json"
            contract_path.write_text(json.dumps(contract, ensure_ascii=False), encoding="utf-8")
            fixture_path.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(script), "--contract", str(contract_path), "--fixture", str(fixture_path)],
                check=False,
                capture_output=True,
                text=True,
            )

        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertNotIn("remote-secret-value", completed.stdout)
        self.assertTrue(json.loads(completed.stdout)["valid"])


if __name__ == "__main__":
    unittest.main()
