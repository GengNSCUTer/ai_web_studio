from __future__ import annotations

import unittest

from app.services.tools.formatter import ExternalContextAssembler
from app.services.tools.schemas import ExternalSource


class ExternalContextAssemblerTest(unittest.TestCase):
    def test_globally_renumbers_parallel_source_labels(self) -> None:
        sources = [
            ExternalSource(
                source_type="weather",
                provider="amap",
                title=city,
                display_text=f"{city} weather",
                citation_label="[W1]",
            )
            for city in ("深圳", "广州")
        ]

        context = ExternalContextAssembler.format_sources_for_prompt(sources, max_chars=2000)

        self.assertIn("[T1]", context or "")
        self.assertIn("[T2]", context or "")
        self.assertEqual([source.citation_label for source in sources], ["[T1]", "[T2]"])
        self.assertEqual([source.metadata["provider_citation_label"] for source in sources], ["[W1]", "[W1]"])

    def test_marks_only_sources_that_enter_prompt_budget(self) -> None:
        sources = [
            ExternalSource(source_type="web", provider="test", title="one", display_text="A" * 100),
            ExternalSource(source_type="web", provider="test", title="two", display_text="B" * 100),
        ]

        ExternalContextAssembler.format_sources_for_prompt(sources, max_chars=80)

        self.assertTrue(sources[0].used_in_prompt)
        self.assertFalse(sources[1].used_in_prompt)

    def test_long_first_result_does_not_starve_later_source(self) -> None:
        sources = [
            ExternalSource(source_type="web", provider="test", title="long", display_text="A" * 2000),
            ExternalSource(source_type="web", provider="test", title="later", display_text="SECOND_SOURCE"),
        ]

        context = ExternalContextAssembler.format_sources_for_prompt(sources, max_chars=800)

        self.assertIn("SECOND_SOURCE", context or "")
        self.assertIn("结果已按上下文预算压缩", context or "")
        self.assertTrue(all(source.used_in_prompt for source in sources))

    def test_library_descriptions_do_not_crowd_out_documentation_conditions(self) -> None:
        """小预算中应保留正文末尾条件，而不是给五条库简介分配证据份额。"""
        libraries = [ExternalSource(source_type="documentation_library", provider="context7",
                                    title=f"library {i}", display_text="简介" * 120) for i in range(5)]
        document = ExternalSource(source_type="documentation", provider="context7", title="API behavior",
                                  display_text="说明" * 60 + "New transaction begins only upon the NEXT_STATEMENT.")
        context = ExternalContextAssembler.format_sources_for_prompt([*libraries, document], max_chars=420)
        self.assertIn("NEXT_STATEMENT", context)
        self.assertIn("[T6]", context)
        self.assertNotIn("library 0", context)
        self.assertTrue(document.used_in_prompt)
        self.assertTrue(all(not source.used_in_prompt for source in libraries))

    def test_catalog_only_and_other_providers_keep_their_results(self) -> None:
        library = ExternalSource(source_type="documentation_library", provider="a", title="Candidate", display_text="Library ID")
        other = ExternalSource(source_type="documentation", provider="b", title="Other", display_text="Independent evidence")
        self.assertIn("Candidate", ExternalContextAssembler.format_sources_for_prompt([library], max_chars=500))
        context = ExternalContextAssembler.format_sources_for_prompt([library, other], max_chars=500)
        self.assertIn("Candidate", context)
        self.assertIn("Independent evidence", context)

    def test_reformat_with_smaller_budget_clears_stale_injection_flags(self) -> None:
        sources = [ExternalSource(source_type="web", provider="a", title=str(i), display_text="evidence" * 40) for i in range(3)]
        ExternalContextAssembler.format_sources_for_prompt(sources, max_chars=2000)
        ExternalContextAssembler.format_sources_for_prompt(sources, max_chars=80)
        self.assertEqual([source.used_in_prompt for source in sources], [True, False, False])
        self.assertIsNone(ExternalContextAssembler.format_sources_for_prompt(sources, max_chars=0))
        self.assertFalse(any(source.used_in_prompt for source in sources))


if __name__ == "__main__":
    unittest.main()
