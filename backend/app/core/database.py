# 导入sqlalchemy数据库引擎创建工具
from sqlalchemy import create_engine
# 导入ORM基类、会话工厂生成工具
from sqlalchemy.orm import declarative_base, sessionmaker

# 导入全局配置，获取数据库连接地址
from app.core.config import settings

# 创建数据库引擎，pool_pre_ping=True 每次连接前校验连接有效性，避免断开失效
engine = create_engine(settings.sqlalchemy_database_uri, pool_pre_ping=True)
# 生成数据库会话工厂，autocommit=False手动提交事务，autoflush=False不自动刷新数据，绑定上面的引擎
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
# ORM模型基础类，所有数据库表实体类都需要继承该Base
Base = declarative_base()