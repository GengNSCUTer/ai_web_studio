"""导入固定来源的公开中文技术资料，并使用现有知识库索引链建立语料库。

脚本只访问白名单中的 Python 官方中文文档页面，不接受任意 URL，避免把
外部页面当成可信知识源。重复文件会按文件名、原文哈希和规范化文本哈希跳过。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import uuid4

from sqlalchemy import select

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.startup import ensure_runtime_schema
from app.models.user import User
from app.repositories.knowledge_repo import (
    KnowledgeBaseRepository,
    KnowledgeChunkRepository,
    KnowledgeDocumentRepository,
    KnowledgeEvalCaseRepository,
    KnowledgeJobRepository,
)
from app.repositories.setting_repo import UserSettingRepository
from app.schemas.knowledge import KnowledgeDocumentCreate
from app.services.file_parser_service import FileParserService
from app.services.knowledge_service import KnowledgeDocumentService


PUBLIC_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("python_asyncio.md", "https://docs.python.org/zh-cn/3/library/asyncio.html", "asyncio"),
    ("python_coroutines.md", "https://docs.python.org/zh-cn/3/library/asyncio-task.html", "协程与任务"),
    ("python_typing.md", "https://docs.python.org/zh-cn/3/library/typing.html", "typing"),
    ("python_logging.md", "https://docs.python.org/zh-cn/3/howto/logging.html", "logging"),
    ("python_http_server.md", "https://docs.python.org/zh-cn/3/library/http.server.html", "HTTP server"),
    ("python_sqlite.md", "https://docs.python.org/zh-cn/3/library/sqlite3.html", "sqlite3"),
)
ALLOWED_SOURCE_URLS = frozenset(item[1] for item in PUBLIC_SOURCES)


class _TextExtractor(HTMLParser):
    """只提取正文文本，丢弃脚本、样式、导航和代码高亮重复节点。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style", "noscript", "nav", "footer"}:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "nav", "footer"} and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip:
            text = re.sub(r"\s+", " ", data).strip()
            if text:
                self.parts.append(text)

    def text(self) -> str:
        return "\n\n".join(self.parts)


def _fetch(url: str) -> str:
    if url not in ALLOWED_SOURCE_URLS:
        raise ValueError("资料来源不在固定白名单中")
    request = Request(url, headers={"User-Agent": "AI-Web-Studio-RAG-Corpus/1.0"})
    with urlopen(request, timeout=30) as response:  # noqa: S310 - URL 已由固定白名单提供
        payload = response.read()
    parser = _TextExtractor()
    parser.feed(payload.decode("utf-8", errors="ignore"))
    content = parser.text()
    if len(content) < 500:
        raise RuntimeError(f"公开页面正文过短：{url}")
    return content[: settings.knowledge_parse_max_chars]


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _text_hash(path: Path) -> str:
    text = re.sub(r"\s+", " ", path.read_text(encoding="utf-8")).strip().casefold()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-email", required=True)
    parser.add_argument("--knowledge-base-id", required=True)
    parser.add_argument("--snapshot-dir", type=Path, default=Path("data/chinese_public_corpus"))
    parser.add_argument("--refresh", action="store_true", help="重新抓取快照；仍会按哈希跳过重复文档")
    parser.add_argument("--offline", action="store_true", help="只使用已有快照，不访问网络")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ensure_runtime_schema()
    snapshot_dir = args.snapshot_dir.resolve()
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict[str, str]] = []
    for file_name, url, title in PUBLIC_SOURCES:
        target = snapshot_dir / file_name
        if args.refresh or not target.exists():
            if args.offline:
                raise SystemExit(f"离线模式缺少资料快照：{target}")
            target.write_text(f"# {title}\n\n来源：{url}\n\n" + _fetch(url), encoding="utf-8")
        manifest.append({"file_name": file_name, "url": url, "title": title, "sha256": _sha256(target)})
    (snapshot_dir / "manifest.json").write_text(
        json.dumps({"source_policy": "fixed_python_official_zh_cn", "documents": manifest}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    if args.dry_run:
        print(json.dumps({"snapshot_dir": str(snapshot_dir), "documents": manifest}, ensure_ascii=False, indent=2))
        return 0

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == args.user_email))
        if not user:
            raise SystemExit("user not found")
        base = KnowledgeBaseRepository(db).get_by_user(args.knowledge_base_id, user.id)
        if not base:
            raise SystemExit("knowledge base not found")
        document_repo = KnowledgeDocumentRepository(db)
        existing = document_repo.list_by_knowledge_base(base.id, user.id)
        existing_names = {item.file_name.casefold() for item in existing}
        existing_raw: set[str] = set()
        existing_text: set[str] = set()
        for item in existing:
            stored = Path(settings.upload_dir) / item.storage_key
            if stored.exists():
                existing_raw.add(_sha256(stored))
                existing_text.add(_text_hash(stored))
        service = KnowledgeDocumentService(
            document_repo=document_repo,
            base_repo=KnowledgeBaseRepository(db),
            job_repo=KnowledgeJobRepository(db),
            chunk_repo=KnowledgeChunkRepository(db),
            setting_repo=UserSettingRepository(db),
            eval_case_repo=KnowledgeEvalCaseRepository(db),
        )
        import_root = Path(settings.upload_dir) / user.id / "knowledge_import"
        import_root.mkdir(parents=True, exist_ok=True)
        results: list[dict[str, object]] = []
        for item in manifest:
            source = snapshot_dir / item["file_name"]
            raw_hash = _sha256(source)
            text_hash = _text_hash(source)
            reasons: list[str] = []
            if source.name.casefold() in existing_names:
                reasons.append("same_file_name")
            if raw_hash in existing_raw:
                reasons.append("same_raw_sha256")
            if text_hash in existing_text:
                reasons.append("same_extracted_text")
            if reasons:
                results.append({"file_name": source.name, "status": "skipped_duplicate", "reasons": reasons})
                continue
            storage_name = f"{uuid4()}_{source.name}"
            target = import_root / storage_name
            shutil.copy2(source, target)
            document = None
            try:
                created = service.add_document(
                    base.id,
                    user.id,
                    KnowledgeDocumentCreate(
                        file_name=source.name,
                        mime_type="text/markdown",
                        file_size=source.stat().st_size,
                        storage_key=f"{user.id}/knowledge_import/{storage_name}",
                    ),
                )
                document = document_repo.get_by_user(created.id, user.id) if created else None
                if not document:
                    raise RuntimeError("document creation failed")
                document.content_hash = raw_hash
                db.commit()
                parsed = service.parse_document(base.id, document.id, user.id)
                indexed = service.index_document(base.id, document.id, user.id) if parsed and parsed.document.parse_status == "parsed" else None
                if not indexed or indexed.document.index_status != "indexed":
                    raise RuntimeError("document indexing failed")
                results.append({"file_name": source.name, "status": "imported", "chunk_count": indexed.chunk_count})
                existing_names.add(source.name.casefold())
                existing_raw.add(raw_hash)
                existing_text.add(text_hash)
            except Exception as exc:  # noqa: BLE001 - 单文件失败不泄露外部凭据
                db.rollback()
                results.append({"file_name": source.name, "status": "failed", "error_type": type(exc).__name__})
        print(json.dumps({"knowledge_base_id": base.id, "documents": results}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
