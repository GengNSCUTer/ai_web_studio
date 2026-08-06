"""Import and index a reviewed PDF batch into an existing knowledge base.

The command is intentionally account-scoped and does not use login credentials.
It compares file names, raw bytes, and normalized extracted text before creating
anything.  Each accepted PDF is copied into the normal upload root, parsed, and
indexed through the same service path used by the application.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
from pathlib import Path
from uuid import uuid4

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.startup import ensure_runtime_schema
from app.models.knowledge import KnowledgeDocument
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
from sqlalchemy import select


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True)
    parser.add_argument("--user-email", required=True)
    parser.add_argument("--knowledge-base-id", required=True)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _text_fingerprint(path: Path, parser: FileParserService) -> str:
    text = parser.parse_file(path, max_chars=settings.knowledge_parse_max_chars) or ""
    normalized = re.sub(r"\s+", " ", text).strip().casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else ""


def main() -> int:
    args = parse_args()
    source_dir = Path(args.source_dir).expanduser().resolve()
    files = sorted(path for path in source_dir.glob("*.pdf") if path.is_file())
    if not files:
        raise SystemExit("source directory contains no PDF files")

    ensure_runtime_schema()
    parser = FileParserService()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == args.user_email))
        if not user:
            raise SystemExit("user not found")
        base_repo = KnowledgeBaseRepository(db)
        knowledge_base = base_repo.get_by_user(args.knowledge_base_id, user.id)
        if not knowledge_base:
            raise SystemExit("knowledge base not found")
        document_repo = KnowledgeDocumentRepository(db)
        existing = document_repo.list_by_knowledge_base(knowledge_base.id, user.id)
        existing_names = {item.file_name.casefold() for item in existing}
        existing_raw: set[str] = set()
        existing_text: set[str] = set()
        for item in existing:
            stored = Path(settings.upload_dir) / item.storage_key
            if stored.exists():
                existing_raw.add(_sha256(stored))
                fingerprint = _text_fingerprint(stored, parser)
                if fingerprint:
                    existing_text.add(fingerprint)

        result: list[dict[str, object]] = []
        accepted: list[tuple[Path, str, str]] = []
        for source in files:
            raw_hash = _sha256(source)
            text_hash = _text_fingerprint(source, parser)
            reasons: list[str] = []
            if source.name.casefold() in existing_names:
                reasons.append("same_file_name")
            if raw_hash in existing_raw:
                reasons.append("same_raw_sha256")
            if text_hash and text_hash in existing_text:
                reasons.append("same_extracted_text")
            if reasons:
                result.append({"file_name": source.name, "status": "skipped_duplicate", "reasons": reasons})
                continue
            accepted.append((source, raw_hash, text_hash))

        if args.dry_run:
            result.extend(
                {"file_name": source.name, "status": "would_import", "bytes": source.stat().st_size}
                for source, _, _ in accepted
            )
            print(json.dumps({"knowledge_base_id": knowledge_base.id, "documents": result}, ensure_ascii=False, indent=2))
            return 0

        service = KnowledgeDocumentService(
            document_repo=document_repo,
            base_repo=base_repo,
            job_repo=KnowledgeJobRepository(db),
            chunk_repo=KnowledgeChunkRepository(db),
            setting_repo=UserSettingRepository(db),
            eval_case_repo=KnowledgeEvalCaseRepository(db),
        )
        import_root = Path(settings.upload_dir) / user.id / "knowledge_import"
        import_root.mkdir(parents=True, exist_ok=True)

        for source, raw_hash, _ in accepted:
            storage_name = f"{uuid4()}_{source.name}"
            target = import_root / storage_name
            shutil.copy2(source, target)
            storage_key = f"{user.id}/knowledge_import/{storage_name}"
            try:
                created = service.add_document(
                    knowledge_base.id,
                    user.id,
                    KnowledgeDocumentCreate(
                        file_name=source.name,
                        mime_type="application/pdf",
                        file_size=source.stat().st_size,
                        storage_key=storage_key,
                    ),
                )
                if not created:
                    raise RuntimeError("knowledge base not found")
                document = document_repo.get_by_user(created.id, user.id)
                if not document:
                    raise RuntimeError("created document could not be reloaded")
                document.content_hash = raw_hash
                db.commit()
                parsed = service.parse_document(knowledge_base.id, document.id, user.id)
                if not parsed or parsed.document.parse_status != "parsed":
                    result.append({"file_name": source.name, "status": "parse_failed", "document_id": document.id})
                    continue
                indexed = service.index_document(knowledge_base.id, document.id, user.id)
                if not indexed or indexed.document.index_status != "indexed":
                    result.append({"file_name": source.name, "status": "index_failed", "document_id": document.id})
                    continue
                result.append(
                    {
                        "file_name": source.name,
                        "status": "imported",
                        "document_id": document.id,
                        "chunk_count": indexed.chunk_count,
                    }
                )
                existing_names.add(source.name.casefold())
                existing_raw.add(raw_hash)
            except Exception as exc:  # noqa: BLE001 - report a safe per-file outcome and continue
                db.rollback()
                result.append(
                    {
                        "file_name": source.name,
                        "status": "failed",
                        "error": "document import failed",
                        "error_type": type(exc).__name__,
                    }
                )

        print(json.dumps({"knowledge_base_id": knowledge_base.id, "documents": result}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
