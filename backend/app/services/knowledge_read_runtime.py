"""知识库检索线程只持有自己创建的短数据库会话。"""

import asyncio
from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session


_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="knowledge-read")


async def run_knowledge_db(bind, operation):
    def execute():
        with Session(bind, autoflush=False, expire_on_commit=False) as db:
            db.execute(text("SET LOCAL statement_timeout = '3s'"))
            try:
                value = operation(db)
                db.expunge_all()
                return value
            except DBAPIError as exc:
                if getattr(exc.orig, "sqlstate", None) == "57014":
                    raise TimeoutError("知识库数据库查询超时") from exc
                raise

    return await asyncio.get_running_loop().run_in_executor(_executor, execute)


class FrozenReadSettings:
    def __init__(self, service, user_id):
        self.config = service.get_or_create_user_settings(user_id)
        self.knowledge_keys = {kind: service.resolve_knowledge_model_api_key(user_id, kind)
                               for kind in ("embedding", "rerank")}
        self.chat_key = service.resolve_provider_api_key(user_id)

    def get_or_create_user_settings(self, user_id):
        return self.config

    def resolve_knowledge_model_api_key(self, user_id, kind):
        return self.knowledge_keys[kind]

    def resolve_provider_api_key(self, user_id):
        return self.chat_key
