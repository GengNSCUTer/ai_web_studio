"""检查旧签名的搜索向量。默认只报告，--apply 把残留送回现有补建队列。"""

import argparse
import json

from sqlalchemy import select, update

from app.core.database import SessionLocal
from app.models.user_memory import UserMemory
from app.models.user_setting import UserSetting
from app.services.memory_retrieval_service import MemoryRetrievalService, embedding_config, lock_embedding_setting


def reconcile(session_factory=SessionLocal, *, apply=False, batch_size=100):
    scanned = stale = 0
    with session_factory() as db:
        user_ids = list(db.scalars(select(UserMemory.user_id).where(UserMemory.search_embedding.is_not(None)).distinct()))
    for user_id in user_ids:
        after = ""
        while True:
            with session_factory() as db:
                setting = lock_embedding_setting(db, user_id) if apply else db.scalar(
                    select(UserSetting).where(UserSetting.user_id == user_id))
                if setting is None:
                    break
                current = MemoryRetrievalService.signature(embedding_config(setting))
                rows = db.execute(select(UserMemory.id, UserMemory.embedding_signature).where(
                    UserMemory.user_id == user_id, UserMemory.search_embedding.is_not(None), UserMemory.id > after
                ).order_by(UserMemory.id).limit(batch_size)).all()
                if not rows:
                    break
                after = rows[-1].id
                scanned += len(rows)
                stale_ids = [row.id for row in rows if row.embedding_signature != current]
                stale += len(stale_ids)
                if apply and stale_ids:
                    db.execute(update(UserMemory).where(UserMemory.user_id == user_id,
                        UserMemory.id.in_(stale_ids)).values(search_embedding=None, search_dimensions=None,
                            embedding_retry_after=None, updated_at=UserMemory.updated_at))
                    db.commit()
    return {"scanned": scanned, "stale": stale, "requeued": stale if apply else 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="将残留分批放回索引补建队列")
    args = parser.parse_args()
    print(json.dumps(reconcile(apply=args.apply), ensure_ascii=False))
