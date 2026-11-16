"""检查真实Markdown/DOCX及缺章反例，不让模型自述代替交付验证。"""

import base64
import io
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile

from pydantic import ValidationError

from app.schemas.document_task import DocumentTaskCreate
from app.services.document_completion import verify_document_draft, verify_document_export
from app.services.document_delivery_service import DocumentDeliveryService


class DocumentCompletionTest(unittest.TestCase):
    def setUp(self):
        self.refs = ["project_file:f / note.md"]
        self.draft = {"text": "# 背景\n\n数据库资料。\n\n## 结论\n\n使用PostgreSQL。", "references": self.refs}

    def draft_verdict(self, text, **kwargs):
        return verify_document_draft(text=text, references=kwargs.get("references", self.refs),
            evidence={"references": self.refs}, required_sections=kwargs.get("required_sections", ["背景", "结论"]))

    def exported(self, fmt="markdown"):
        text = DocumentDeliveryService._markdown_with_sources(**{"content": self.draft["text"], "references": self.refs})
        request = {"format": fmt, "file_name": "报告.md" if fmt == "markdown" else "报告.docx"}
        if fmt == "markdown":
            data, mime = text.encode(), "text/markdown; charset=utf-8"
        else:
            with tempfile.TemporaryDirectory() as root:
                path = Path(root) / "报告.docx"
                DocumentDeliveryService._render_docx(self.draft["text"], self.refs, path)
                data = path.read_bytes()
            mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        result = {"text": text, "references": self.refs, "file_name": request["file_name"], "mime_type": mime,
            "data_base64": base64.b64encode(data).decode()}
        return request, result

    def test_required_sections_need_actual_content_not_claim_or_toc(self):
        for text in ("报告已经全部完成。", "目录：背景、结论", "# 背景\n\n# 结论", "```markdown\n# 背景\n正文\n# 结论\n正文\n```", "# 背景\n正文\n# 结论", "    # 背景\n    正文\n    # 结论\n    正文"):
            with self.subTest(text=text):
                self.assertEqual(self.draft_verdict(text).status, "blocked")
        self.assertEqual(self.draft_verdict(self.draft["text"]).status, "complete")

    def test_nested_sections_and_code_body_are_content(self):
        text = "# 背景\n## 数据库\nPostgreSQL\n# 结论\n```python\nprint('ok')\n```"
        self.assertEqual(self.draft_verdict(text).status, "complete")
        self.assertEqual(self.draft_verdict("# C#\n实现说明。", required_sections=["C#"]).status, "complete")

    def test_no_sections_requested_does_not_invent_requirements(self):
        self.assertEqual(self.draft_verdict("普通说明", required_sections=[]).status, "complete")
        self.assertEqual(self.draft_verdict("", required_sections=[]).status, "blocked")

    def test_unknown_or_missing_sources_rejected(self):
        for refs in ([], ["invented source"], [*self.refs, "other"]):
            self.assertIn("authorized_source_manifest", self.draft_verdict(self.draft["text"], references=refs).missing)

    def test_markdown_and_real_docx_roundtrip(self):
        for fmt in ("markdown", "docx"):
            request, result = self.exported(fmt)
            verdict = verify_document_export(result=result, request=request, draft=self.draft)
            self.assertEqual(verdict.status, "complete")
            self.assertFalse(verdict.answer_semantics_verified)

    def test_export_missing_bytes_mismatched_format_or_sources(self):
        request, result = self.exported()
        for mutation in ({"data_base64": ""}, {"data_base64": "***"}, {"file_name": "other.md"},
                         {"references": []}, {"mime_type": "text/html"}, {"text": "different"},
                         {"data_base64": base64.b64encode(b"different").decode()}):
            with self.subTest(mutation=mutation):
                self.assertEqual(verify_document_export(result={**result, **mutation}, request=request, draft=self.draft).status, "blocked")

    def test_openable_but_wrong_docx_rejected(self):
        request, result = self.exported("docx")
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "wrong.docx"
            DocumentDeliveryService._render_docx("wrong body", self.refs, path)
            result["data_base64"] = base64.b64encode(path.read_bytes()).decode()
        self.assertIn("readable_export_matching_draft", verify_document_export(result=result, request=request, draft=self.draft).missing)

    def test_invalid_docx_and_zip_bomb_are_rejected_before_parse(self):
        request, result = self.exported("docx")
        for data in (b"not a docx", b"PK\x03\x04", self.large_zip()):
            result["data_base64"] = base64.b64encode(data).decode()
            self.assertEqual(verify_document_export(result=result, request=request, draft=self.draft).status, "blocked")

    @staticmethod
    def large_zip():
        data = io.BytesIO()
        from zipfile import ZIP_DEFLATED
        with ZipFile(data, "w", compression=ZIP_DEFLATED) as archive:
            archive.writestr("word/document.xml", "x" * 8_000_001)
        return data.getvalue()

    def test_section_input_is_bounded_and_normalized(self):
        payload = DocumentTaskCreate(project_id="p", idempotency_key="k", required_sections=[" 背景 ", "背景", "结论"])
        self.assertEqual(payload.required_sections, ["背景", "结论"])
        for sections in ([""], ["line\nbreak"], ["x" * 81], [str(i) for i in range(13)]):
            with self.assertRaises(ValidationError):
                DocumentTaskCreate(project_id="p", idempotency_key="k", required_sections=sections)
