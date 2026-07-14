#!/usr/bin/env python3
"""Backfill active-generation Chunk embeddings into PostgreSQL/pgvector."""

import argparse

from sqlalchemy import select

from app.core.database import SessionLocal
from app.core.startup import ensure_runtime_schema
from app.models import KnowledgeBase
from app.repositories.knowledge_repo import KnowledgeChunkRepository, KnowledgeDocumentRepository
from app.repositories.setting_repo import UserSettingRepository
from app.services.knowledge_index_service import KnowledgeIndexService
from app.services.setting_service import SettingService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knowledge-base-id", help="Only backfill one knowledge base")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report active Chunk counts without calling the Embedding provider",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ensure_runtime_schema()
    with SessionLocal() as db:
        statement = select(KnowledgeBase).order_by(KnowledgeBase.created_at.asc())
        if args.knowledge_base_id:
            statement = statement.where(KnowledgeBase.id == args.knowledge_base_id)
        knowledge_bases = list(db.scalars(statement).all())

        total_backfilled = 0
        for knowledge_base in knowledge_bases:
            chunk_repo = KnowledgeChunkRepository(db)
            active_generation = knowledge_base.active_index_generation or "legacy"
            active_chunks = chunk_repo.list_by_knowledge_base(
                knowledge_base.id,
                knowledge_base.user_id,
                index_generation=active_generation,
            )
            pending_count = sum(
                not KnowledgeIndexService._has_reusable_embedding(
                    chunk=chunk,
                    knowledge_base=knowledge_base,
                )
                for chunk in active_chunks
            )
            print(
                f"knowledge_base={knowledge_base.id} generation={active_generation} "
                f"chunks={len(active_chunks)} pending={pending_count}"
            )
            if args.dry_run or pending_count == 0:
                continue

            service = KnowledgeIndexService(
                chunk_repo=chunk_repo,
                document_repo=KnowledgeDocumentRepository(db),
                setting_service=SettingService(UserSettingRepository(db)),
            )
            total_backfilled += service.backfill_active_generation_embeddings(
                user_id=knowledge_base.user_id,
                knowledge_base=knowledge_base,
            )

        print(
            f"knowledge_bases={len(knowledge_bases)} backfilled={total_backfilled} "
            f"dry_run={args.dry_run} source=provider"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
