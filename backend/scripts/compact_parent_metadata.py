"""默认只预览；显式--apply无损整理指定用户旧父块metadata。"""

import argparse
import json

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.user import User
from app.models.knowledge import KnowledgeChunk
from app.services.knowledge_parent_context import compact_parent_metadata


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-email", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == args.user_email))
        if not user:
            raise SystemExit("用户不存在。")
        query = select(KnowledgeChunk).where(KnowledgeChunk.user_id == user.id,
            KnowledgeChunk.metadata_json.contains('"parent_child"')).order_by(KnowledgeChunk.id)
        if args.apply:
            query = query.with_for_update()
        report = compact_parent_metadata(list(db.scalars(query)), apply=args.apply)
        if args.apply:
            db.commit()
        else:
            db.rollback()
        print(json.dumps({"applied": args.apply, **report}, ensure_ascii=False))


if __name__ == "__main__":
    main()
