from __future__ import annotations

import unittest

from app.services.tools.evidence_projection_profile import (
    EVIDENCE_PROJECTION_PROFILE_VERSION,
    validate_evidence_projection_profile,
)


class EvidenceProjectionProfileTest(unittest.TestCase):
    def test_missing_profile_fails_closed(self) -> None:
        self.assertEqual(
            validate_evidence_projection_profile(None),
            {
                "version": EVIDENCE_PROJECTION_PROFILE_VERSION,
                "mode": "none",
            },
        )

    def test_bounded_excerpt_normalizes_a_reviewed_canonical_profile(self) -> None:
        profile = validate_evidence_projection_profile(
            {
                "version": EVIDENCE_PROJECTION_PROFILE_VERSION,
                "mode": "bounded_excerpt",
                "allowed_source_types": ["web", "web"],
                "content_paths": ["/metadata/raw/content"],
                "max_sources": 3,
                "max_chars_per_source": 600,
                "max_total_chars": 1500,
            }
        )

        self.assertEqual(profile["allowed_source_types"], ["web"])
        self.assertEqual(profile["max_sources"], 3)
        self.assertEqual(profile["max_chars_per_source"], 600)
        self.assertEqual(profile["max_total_chars"], 1500)

    def test_excerpt_rejects_display_text_wildcards_and_unbounded_limits(self) -> None:
        invalid_profiles = [
            {
                "version": EVIDENCE_PROJECTION_PROFILE_VERSION,
                "mode": "bounded_excerpt",
                "allowed_source_types": ["web"],
                "content_paths": ["/display_text"],
            },
            {
                "version": EVIDENCE_PROJECTION_PROFILE_VERSION,
                "mode": "bounded_excerpt",
                "allowed_source_types": ["web"],
                "content_paths": ["/metadata/raw/*"],
            },
            {
                "version": EVIDENCE_PROJECTION_PROFILE_VERSION,
                "mode": "bounded_excerpt",
                "allowed_source_types": ["web"],
                "content_paths": ["/metadata/raw/content"],
                "max_total_chars": 999_999,
            },
        ]

        for profile in invalid_profiles:
            with self.subTest(profile=profile):
                with self.assertRaises(ValueError):
                    validate_evidence_projection_profile(profile)

    def test_non_excerpt_modes_cannot_smuggle_excerpt_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not accept excerpt fields"):
            validate_evidence_projection_profile(
                {
                    "version": EVIDENCE_PROJECTION_PROFILE_VERSION,
                    "mode": "facts_only",
                    "allowed_source_types": ["web"],
                    "fact_paths": {"content": "/metadata/raw/content"},
                    "content_paths": ["/metadata/raw/content"],
                }
            )


if __name__ == "__main__":
    unittest.main()
