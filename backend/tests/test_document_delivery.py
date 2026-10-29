"""文档草稿生成、来源保留、格式导出和路径权限测试。"""

from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from app.services.document_delivery_service import DocumentDeliveryService, DocumentDeliveryError


class DocumentDeliveryTest(unittest.TestCase):
    def test_markdown_contains_sources_and_normalizes_suffix(self):
        service = DocumentDeliveryService.__new__(DocumentDeliveryService)
        self.assertEqual(service._safe_file_name("brief.txt", "markdown"), "brief.md")
        content = service._markdown_with_sources("# 标题", ["KB1 / paper.pdf / chunk=3"])
        self.assertIn("## 来源", content)
        self.assertIn("KB1 / paper.pdf / chunk=3", content)

    def test_sensitive_path_is_rejected(self):
        service = DocumentDeliveryService.__new__(DocumentDeliveryService)
        self.assertEqual(service._safe_file_name("../draft.md", "markdown"), "draft.md")
        with self.assertRaises(DocumentDeliveryError):
            service._safe_file_name("", "markdown")

    def test_docx_render_creates_openable_file(self):
        service = DocumentDeliveryService.__new__(DocumentDeliveryService)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "brief.docx"
            service._render_docx("# 标题\n\n正文", ["KB1"], target)
            self.assertGreater(target.stat().st_size, 0)
            from docx import Document
            document = Document(str(target))
            self.assertIn("标题", "\n".join(p.text for p in document.paragraphs))


if __name__ == "__main__":
    unittest.main()
