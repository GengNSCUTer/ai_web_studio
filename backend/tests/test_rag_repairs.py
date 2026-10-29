"""覆盖错误标签、精排分数和演示文稿顺序的真实失败场景。"""

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from zipfile import ZipFile

from app.services.file_parser_service import FileParserService
from app.services.knowledge_index_service import RetrievalResult
from app.services.knowledge_retrieval_pipeline import KnowledgeRetrievalPipeline
from scripts.import_rag_gold_set import _resolve_case


class RerankScoreRepairTest(unittest.TestCase):
    def test_success_keeps_low_rerank_scores_but_fallback_uses_original_threshold(self):
        model = Mock(rerank=AsyncMock(return_value=[(1, 0.04), (0, 0.03)]))
        pipeline = KnowledgeRetrievalPipeline(chunk_repo=Mock(), setting_service=Mock(), rerank_service=model)
        base = SimpleNamespace(rerank_enabled=True, rerank_top_n=2,
                               score_threshold=0.8, rerank_model="test")
        items = [RetrievalResult(chunk=SimpleNamespace(id=str(i), content="证据"),
                                 score=score, metadata={}) for i, score in enumerate([0.9, 0.4])]
        async def run():
            return await pipeline._rerank_if_needed(user_id="u", knowledge_base=base,
                                                  query="概览", results=items, result_limit=2)
        ranked = asyncio.run(run())
        self.assertEqual([r.chunk.id for r in ranked], ["1", "0"])
        self.assertEqual(ranked[0].rerank_score, 0.04)
        model.rerank.side_effect = TimeoutError()
        fallback = asyncio.run(run())
        self.assertEqual([r.chunk.id for r in fallback], ["0"])
        self.assertTrue(fallback[0].metadata["rerank_fallback"])


class GoldAnswerabilityRepairTest(unittest.TestCase):
    def resolve(self, case):
        repo = Mock()
        result = _resolve_case(case=case, documents=[], chunk_repo=repo,
                               user_id="u", knowledge_base=SimpleNamespace(id="b", active_index_generation="legacy"))
        repo.list_by_document.assert_not_called()
        return result

    def test_negative_case_accepts_no_positive_targets(self):
        self.assertEqual(self.resolve(dict(case_id="negative", answerable=False)), ([], []))

    def test_negative_case_rejects_fake_targets(self):
        with self.assertRaises(ValueError):
            self.resolve(dict(answerable=False, expected_chunk_selectors=[dict(contains="Redis")]))

    def test_positive_case_still_requires_evidence(self):
        with self.assertRaises(ValueError):
            self.resolve(dict(answerable=True))


class PptxOrderRepairTest(unittest.TestCase):
    def test_order_comes_from_presentation_relationships_not_filenames(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "slides.pptx"
            with ZipFile(path, "w") as archive:
                archive.writestr("ppt/presentation.xml", '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><p:sldIdLst><p:sldId id="1" r:id="r2"/><p:sldId id="2" r:id="r10"/><p:sldId id="3" r:id="r1"/></p:sldIdLst></p:presentation>')
                archive.writestr("ppt/_rels/presentation.xml.rels", '<Relationships>' + ''.join(f'<Relationship Id="r{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slide" Target="slides/slide{i}.xml"/>' for i in [1, 2, 10]) + '</Relationships>')
                for i in [1, 2, 10]:
                    archive.writestr(f"ppt/slides/slide{i}.xml", f'<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:p><a:r><a:t>正文{i}</a:t></a:r><a:r><a:t>Redis</a:t></a:r></a:p></p:sld>')
            text = FileParserService().parse_file(path)
            self.assertLess(text.index("正文2Redis"), text.index("正文10Redis"))
            self.assertLess(text.index("正文10Redis"), text.index("正文1Redis"))
            self.assertIn("## 幻灯片 3", text)


if __name__ == "__main__":
    unittest.main()
