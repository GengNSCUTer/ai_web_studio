"""固定账号、真实 Embedding 验证不同问题的记忆召回；只清理本次临时资料。"""

import asyncio
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import app.models  # noqa: F401
from sqlalchemy import delete, select

from app.core.database import SessionLocal
from app.models.project import Project
from app.models.user import User
from app.models.user_memory import UserMemory
from app.services.memory_index_service import MemoryIndexWorker
from app.services.memory_retrieval_service import MemoryRetrievalService


async def run():
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == os.getenv("AIWS_E2E_USER_EMAIL", "1528713326@qq.com")))
        uid = user.id
        project = Project(user_id=uid, name=f"记忆索引语义验收-{uuid4().hex[:8]}")
        db.add(project)
        db.flush()
        pid = project.id
        cases = [
            ("技术选型", "软件选型优先使用开源且能够自行部署的方案。", "商业闭源 SaaS 和自托管产品该优先考虑哪个？"),
            ("差旅习惯", "出差通常乘坐高铁，尽量避免航班。", "跨城交通应该优先订火车票还是机票？"),
            ("饮食限制", "对花生过敏，餐饮安排要避开花生及其制品。", "公司聚餐有什么食材必须排除？"),
        ]
        ids = []
        for title, content, query in cases:
            row = UserMemory(user_id=uid, project_id=pid, title=title, content=content, status="active", is_enabled=True)
            db.add(row)
            db.flush()
            ids.append(row.id)
        db.commit()
    records = []
    try:
        # 网络在 Worker 的事务外；这不是用本地伪向量代替真实模型。
        await MemoryIndexWorker().run_once()
        reader = MemoryRetrievalService(timeout_seconds=20)
        for i, (_, _, query) in enumerate(cases):
            result = await reader.retrieve(user_id=uid, project_id=pid, query=query, max_chars=4000)
            matched = next((r for r in result.records if r["id"] == ids[i]), None)
            assert matched, f"语义样例 {i+1} 未召回"
            records.append(dict(case=i+1, passed=True, reason=matched["reason"],
                semantic_score=matched["semantic_score"], read_rows=result.diagnostics["read_rows"],
                search=result.diagnostics.get("database_search"), cache=result.diagnostics["read_cache"]))
        again = await reader.retrieve(user_id=uid, project_id=pid, query=cases[0][2], max_chars=4000)
        assert again.diagnostics["read_cache"] == "hit"
        with SessionLocal() as db:
            db.get(UserMemory, ids[0]).status = "revoked"
            db.commit()
        revoked = await reader.retrieve(user_id=uid, project_id=pid, query=cases[0][2], max_chars=4000)
        assert ids[0] not in [r["id"] for r in revoked.records]
        print(json.dumps(dict(cases=records, cache_hit=True, revocation_checked=True,
                              online_embedding=True), ensure_ascii=False, indent=2))
    finally:
        with SessionLocal() as db:
            db.execute(delete(UserMemory).where(UserMemory.id.in_(ids), UserMemory.user_id == uid))
            db.execute(delete(Project).where(Project.id == pid, Project.user_id == uid))
            db.commit()


if __name__ == "__main__":
    asyncio.run(run())
