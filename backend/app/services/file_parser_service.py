from pathlib import Path
from pathlib import PurePosixPath
import posixpath
from xml.etree import ElementTree as ET
from zipfile import ZipFile

from docx import Document
from pypdf import PdfReader


class FileParserService:
    SUPPORTED_TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".docx"}
    SUPPORTED_PDF_EXTENSIONS = {".pdf"}
    MAX_PARSED_TEXT_CHARS = 24000

    def parse_file(self, file_path: Path, max_chars: int | None = None) -> str | None:
        suffix = file_path.suffix.lower()
        if suffix in self.SUPPORTED_TEXT_EXTENSIONS:
            return self._truncate(self._parse_text_file(file_path), max_chars=max_chars)
        if suffix in self.SUPPORTED_PDF_EXTENSIONS:
            return self._truncate(self._parse_pdf(file_path), max_chars=max_chars)
        if suffix == ".pptx":
            return self._truncate(self._parse_pptx(file_path), max_chars=max_chars)
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

    def _parse_pptx(self, file_path: Path) -> str:
        # 使用演示文稿关系表中的展示顺序；文件名数字顺序也不一定等于用户排序。
        p = "{http://schemas.openxmlformats.org/presentationml/2006/main}"
        a = "{http://schemas.openxmlformats.org/drawingml/2006/main}"
        r = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
        with ZipFile(file_path) as archive:
            if sum(item.file_size for item in archive.infolist()) > 128 * 1024 * 1024:
                raise ValueError("PPTX 解压后大小超过解析限制")
            presentation = ET.fromstring(archive.read("ppt/presentation.xml"))
            relationships = ET.fromstring(archive.read("ppt/_rels/presentation.xml.rels"))
            targets = {item.get("Id"): item for item in relationships}
            parts: list[str] = []
            for page, slide in enumerate(presentation.iter(p + "sldId"), 1):
                relation = targets.get(slide.get(r + "id"))
                if relation is None or relation.get("TargetMode") == "External":
                    raise ValueError("PPTX 幻灯片关系缺失或包含外部目标")
                target = relation.get("Target", "")
                if not relation.get("Type", "").endswith("/slide") or not target:
                    raise ValueError("PPTX 幻灯片关系类型不合法")
                path = posixpath.normpath(target.lstrip("/") if target.startswith("/") else "ppt/" + target)
                if not path.startswith("ppt/slides/") or ".." in PurePosixPath(path).parts:
                    raise ValueError("PPTX 幻灯片路径越界")
                root = ET.fromstring(archive.read(path))
                paragraphs = []
                for paragraph in root.iter(a + "p"):
                    # 同一段落的 run 直接连接，避免给英文标识符人为插入空格。
                    text = "".join(node.text or "" for node in paragraph.iter(a + "t")).strip()
                    if text:
                        paragraphs.append(text)
                if paragraphs:
                    parts.append(f"## 幻灯片 {page}\n\n" + "\n".join(paragraphs))
            return "\n\n".join(parts)

    def _truncate(self, content: str, max_chars: int | None = None) -> str | None:
        normalized = content.strip()
        if not normalized:
            return None
        return normalized[: (max_chars or self.MAX_PARSED_TEXT_CHARS)]
