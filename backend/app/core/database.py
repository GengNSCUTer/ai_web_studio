import os

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from app.core.config import settings
from app.core.database_pool import resolve_database_pool_budget

# 配置加载 .env 后再计算预算。池在本进程按需打开连接。
database_pool_budget = resolve_database_pool_budget(os.getenv("AIWS_DB_ROLE", "cli"), os.environ)
engine = create_engine(settings.sqlalchemy_database_uri, **database_pool_budget.sqlalchemy_options())
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()
