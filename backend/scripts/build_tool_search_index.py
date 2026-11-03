"""显式预建账号可见工具索引；不执行工具，不修改权限或审批状态。"""

import argparse
import asyncio
import json

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.user import User
from app.repositories.project_repo import ProjectRepository
from app.services.tools.catalog import ToolCatalog
from app.services.tools.semantic_retrieval import ToolSemanticRetriever


async def run(email, project_id):
    # 仅在短事务读取目录，随后关闭Session再等在线Embedding。
    with SessionLocal() as db:
        user_id = db.scalar(select(User.id).where(User.email == email))
        if not user_id:
            raise RuntimeError("账号不存在。")
        if project_id and not ProjectRepository(db).get_by_user(project_id, user_id):
            raise RuntimeError("项目不存在或不属于该账号。")
        definitions = [tool for tool in ToolCatalog(db=db, user_id=user_id, project_id=project_id).list_definitions()
                       if tool.enabled_by_default]
    result = await ToolSemanticRetriever(user_id=user_id).warmup(definitions)
    print(json.dumps(result, ensure_ascii=False))
    if not result["index_complete"]:
        raise RuntimeError("预建期间工具发生变化，请重试。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", required=True)
    parser.add_argument("--project-id")
    args = parser.parse_args()
    try:
        asyncio.run(run(args.email, args.project_id))
    except Exception:
        # 配置和远端异常可能含凭据，命令只报告安全错误类别。
        parser.exit(1, "工具索引预建失败，请检查迁移、在线Embedding配置和项目权限。\n")
