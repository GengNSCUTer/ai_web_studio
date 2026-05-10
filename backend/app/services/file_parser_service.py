from pathlib import Path

from docx import Document
from pypdf import PdfReader


class FileParserService:
    SUPPORTED_TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".docx"}
    SUPPORTED_PDF_EXTENSIONS = {".pdf"}
    MAX_PARSED_TEXT_CHARS = 24000

    def parse_file(self, file_path: Path) -> str | None:
        suffix = file_path.suffix.lower()
        if suffix in self.SUPPORTED_TEXT_EXTENSIONS:
            return self._truncate(self._parse_text_file(file_path))
        if suffix in self.SUPPORTED_PDF_EXTENSIONS:
            return self._truncate(self._parse_pdf(file_path))
        return None

    def _parse_text_file(self, file_path: Path) -> str:
        if file_path.suffix.lower() == ".docx":
            return self._parse_docx(file_path)
        return file_path.read_text(encoding="utf-8", errors="ignore").strip()

    def _parse_docx(self, file_path: Path) -> str:
        document = Document(str(file_path))
        parts = [paragraph.text.strip() for paragraph in document.paragraphs if paragraph.text.strip()]
        return "\n".join(parts).strip()

    def _parse_pdf(self, file_path: Path) -> str:
        reader = PdfReader(str(file_path))
        parts: list[str] = []
        for page in reader.pages:
            text = page.extract_text() or ""
            if text.strip():
                parts.append(text.strip())
        return "\n\n".join(parts).strip()

    def _truncate(self, content: str) -> str | None:
        normalized = content.strip()
        if not normalized:
            return None
        return normalized[: self.MAX_PARSED_TEXT_CHARS]
