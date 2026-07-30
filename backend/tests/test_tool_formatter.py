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


if __name__ == "__main__":
    unittest.main()
