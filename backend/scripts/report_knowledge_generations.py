"""只读列出可能回收的索引版本及其占用。"""

import json
from datetime import timedelta

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import aliased

from app.core.database import SessionLocal
from app.models.knowledge import KnowledgeBase, KnowledgeChunk, KnowledgeIndexGeneration, KnowledgeJob
from app.services.knowledge_index_service import KnowledgeLexicalStore
from app.services.knowledge_job_runtime import utcnow


def report(session_factory=SessionLocal):
    now = utcnow()
    cutoff = now - timedelta(hours=24)
    building = aliased(KnowledgeIndexGeneration)
    generation = KnowledgeIndexGeneration
    used_by_running = select(building.id).join(KnowledgeJob, KnowledgeJob.id == building.job_id).where(
        building.knowledge_base_id == generation.knowledge_base_id,
        or_(building.base_generation_id == generation.id, building.id == generation.id),
        KnowledgeJob.status == "running", KnowledgeJob.lease_expires_at > now,
    ).exists()
    lookup = select(generation.id, generation.knowledge_base_id, generation.status,
        func.count(KnowledgeChunk.id).label("chunk_rows")).join(
        KnowledgeBase, KnowledgeBase.id == generation.knowledge_base_id).outerjoin(KnowledgeChunk,
        and_(KnowledgeChunk.knowledge_base_id == generation.knowledge_base_id,
             KnowledgeChunk.index_generation == generation.id)).where(
        KnowledgeBase.active_index_generation != generation.id,
        func.coalesce(generation.activated_at, generation.created_at) <= cutoff, ~used_by_running,
    ).group_by(generation.id, generation.knowledge_base_id, generation.status).order_by(generation.id)
    with session_factory() as db:
        if db.get_bind().dialect.name == "postgresql":
            db.execute(text("SET TRANSACTION READ ONLY"))
        rows = db.execute(lookup).all()
    stores = [KnowledgeLexicalStore(), KnowledgeLexicalStore(contextual=True)]
    result = []
    for row in rows:
        paths = [store.index_path(row.knowledge_base_id, generation_id=row.id) for store in stores]
        bm25_bytes = sum(path.stat().st_size for path in paths if path.is_file())
        result.append(dict(generation_id=row.id, knowledge_base_id=row.knowledge_base_id,
                           status=row.status, chunk_rows=row.chunk_rows, bm25_bytes=bm25_bytes))
    return result


if __name__ == "__main__":
    print(json.dumps({"candidates": report()}, ensure_ascii=False, indent=2))
