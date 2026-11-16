"""文档交付的确定性检查，不判断事实真伪，也不授予工作区写入权限。"""

import base64
import binascii
import io
import re
from zipfile import BadZipFile, ZipFile

from docx import Document
from docx.opc.exceptions import OpcError
from lxml.etree import XMLSyntaxError

from app.services.document_delivery_service import DocumentDeliveryService
from app.services.tools.completion_verifier import CompletionVerdict


def _sections(text):
    """只把代码围栏外的标题算作章节，空标题或空章节不满足要求。"""
    sections = {}
    titles = []
    fence = None
    for line in text.splitlines():
        stripped = line.strip()
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})", line)
        if marker:
            token = marker[1]
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence) and not line[marker.end():].strip():
                fence = None
            continue
        heading = re.match(r"^ {0,3}(#{1,6})[ \t]+(.+?)\s*$", line) if fence is None else None
        if heading:
            level = len(heading[1])
            titles = [(depth, name) for depth, name in titles if depth < level]
            # 闭合#之前必须有空格，不能把C#等合法标题的结尾剥掉。
            title = re.sub(r"[ \t]+#+$", "", heading[2]).strip().casefold()
            titles.append((level, title))
            sections.setdefault(title, False)
        elif stripped:
            for _, title in titles:
                sections[title] = True
    return sections


def verify_document_draft(*, text, references, evidence, required_sections=()):
    """章节来自用户的结构化输入；来源必须等于已保存的授权取证清单。"""
    missing = []
    if not isinstance(text, str) or not text.strip() or len(text) > 100_000:
        missing.append("draft_body")
        text = ""
    sections = _sections(text)
    for section in required_sections:
        if not sections.get(section.casefold(), False):
            missing.append(f"section:{section}")
    expected = evidence.get("references")
    if not isinstance(expected, list) or not expected or references != expected:
        missing.append("authorized_source_manifest")
    return CompletionVerdict(status="blocked" if missing else "complete",
        satisfied=tuple(key for key in ("draft_body", "authorized_source_manifest") if key not in missing),
        missing=tuple(missing), evidence_refs=(), reasons=("document_structure_incomplete",) if missing else (),
        assessment_scope="document_structure_and_export", assessment_stage="after_document_draft")


def verify_document_export(*, result, request, draft):
    """重新解析真实导出字节，并与已保存草稿的渲染内容比较。"""
    missing = []
    expected_text = DocumentDeliveryService._markdown_with_sources(draft["text"], draft["references"])
    if result.get("text") != expected_text or result.get("references") != draft["references"]:
        missing.append("export_content_and_sources")
    fmt = request["format"]
    mime = "text/markdown; charset=utf-8" if fmt == "markdown" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if result.get("file_name") != request["file_name"] or result.get("mime_type") != mime:
        missing.append("export_format_and_name")
    try:
        data = base64.b64decode(result.get("data_base64", ""), validate=True)
        if not data or len(data) > 1_000_000:
            raise ValueError("导出字节为空或超限")
        if fmt == "markdown":
            if data.decode("utf-8") != expected_text:
                raise ValueError("Markdown与草稿不一致")
        else:
            with ZipFile(io.BytesIO(data)) as archive:
                members = archive.infolist()
                # 先限制解压规模，再交给DOCX解析器，避免压缩包放大。
                if len(members) > 2000 or sum(item.file_size for item in members) > 8_000_000:
                    raise ValueError("DOCX解压规模超限")
                if any(item.filename.endswith("vbaProject.bin") for item in members):
                    raise ValueError("不允许宏文档")
            actual = [p.text for p in Document(io.BytesIO(data)).paragraphs if p.text.strip()]
            expected = []
            for line in draft["text"].splitlines():
                line = line.strip()
                if line:
                    expected.append(re.sub(r"^(?:#{1,3}|-) ", "", line).strip())
            if draft["references"]:
                expected.extend(["来源", *draft["references"]])
            if actual != expected:
                raise ValueError("DOCX正文或来源与草稿不一致")
    except (ValueError, TypeError, KeyError, binascii.Error, BadZipFile, OSError, OpcError, XMLSyntaxError):
        missing.append("readable_export_matching_draft")
    return CompletionVerdict(status="blocked" if missing else "complete",
        satisfied=tuple(key for key in ("export_content_and_sources", "export_format_and_name", "readable_export_matching_draft") if key not in missing),
        missing=tuple(missing), evidence_refs=(), reasons=("document_export_invalid",) if missing else (),
        assessment_scope="document_structure_and_export", assessment_stage="after_document_export")
