# 导入生成器类型注解
from collections.abc import Generator
# JWT令牌解码工具
import jwt
# FastAPI依赖注入、异常、状态码
from fastapi import Depends, HTTPException, status
# Bearer Token鉴权工具
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
# SQLAlchemy数据库会话类型
from sqlalchemy.orm import Session

# 数据库会话工厂
from app.core.database import SessionLocal
# 全局配置文件
from app.core.config import settings
# 用户ORM模型
from app.models.user import User
# 用户数据仓库类
from app.repositories.user_repo import UserRepository

# 定义Bearer Token认证方案，auto_error=False手动处理无token场景
bearer_scheme = HTTPBearer(auto_error=False)

# 数据库会话依赖生成器，路由注入获取DB会话
def get_db() -> Generator[Session, None, None]:
    # 创建数据库会话实例
    db = SessionLocal()
    try:
        # 产出会话对象供接口使用
        yield db
    finally:
        # 请求结束后关闭数据库连接
        db.close()

# 鉴权依赖：解析token并查询返回当前登录用户实体
def get_current_user(
    # 注入请求头中的Bearer凭证，允许为空
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    # 注入数据库会话
    db: Session = Depends(get_db),
) -> User:
    # 请求未携带Token，抛出401未认证异常
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    try:
        # 使用密钥与加密算法解码JWT令牌
        payload = jwt.decode(
            credentials.credentials,
            settings.auth_secret_key,
            algorithms=[settings.auth_algorithm],
        )
    # 捕获令牌过期、签名错误等所有token异常
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from exc

    # 从token载荷取出用户唯一标识sub
    user_id = payload.get("sub")
    # 载荷无用户ID，判定为非法token
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    # 通过用户ID查询数据库用户记录
    user = UserRepository(db).get_by_id(user_id)
    # 用户不存在，抛出401
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    # 校验通过，返回当前登录用户对象
    return user