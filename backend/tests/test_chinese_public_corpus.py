"""中文公开语料导入脚本的边界测试。"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest


BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from scripts.import_chinese_public_corpus import ALLOWED_SOURCE_URLS, PUBLIC_SOURCES, _fetch  # noqa: E402


class ChinesePublicCorpusTest(unittest.TestCase):
    def test_sources_are_fixed_and_unique(self) -> None:
        self.assertEqual(len(PUBLIC_SOURCES), len(ALLOWED_SOURCE_URLS))
        self.assertTrue(all(url.startswith("https://docs.python.org/zh-cn/") for url in ALLOWED_SOURCE_URLS))

    def test_fetch_rejects_unapproved_url_before_network(self) -> None:
        with self.assertRaises(ValueError):
            _fetch("https://example.com/not-approved")

    def test_gold_set_has_unique_cases_and_no_empty_queries(self) -> None:
        fixture = json.loads((BACKEND / "evals/chinese_public_rag_gold_set.json").read_text(encoding="utf-8"))
        cases = fixture["cases"]
        self.assertEqual(len(cases), len({case["case_id"] for case in cases}))
        self.assertTrue(all(case["query"].strip() for case in cases))
        self.assertGreaterEqual(sum("no-answer" in case.get("tags", []) for case in cases), 2)


if __name__ == "__main__":
    unittest.main()
