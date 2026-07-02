from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import HTTPException, status
from sqlalchemy.exc import IntegrityError

from app.core.config import settings
from app.models.user import User
from app.repositories.user_repo import UserRepository
from app.schemas.auth import TokenResponse, UserLoginRequest, UserRegisterRequest, UserResponse


class AuthService:
    """认证业务层：负责身份规则、密码哈希、JWT 生成，不直接暴露 HTTP 路由。"""

    def __init__(self, repo: UserRepository):
        self.repo = repo

    def register_user(self, payload: UserRegisterRequest) -> TokenResponse:
        # 注册前先做友好去重；数据库唯一索引负责兜住并发注册的最终一致性。
        existing_user = self.repo.find_existing_identity(
            email=str(payload.email),
            username=payload.username,
        )
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Username or email already exists",
            )

        user = User(
            username=payload.username.strip(),
            email=str(payload.email).strip().lower(),
            password_hash=self.hash_password(payload.password),
        )
        try:
            created = self.repo.create(user)
            self.repo.db.commit()
            self.repo.db.refresh(created)
        except IntegrityError as exc:
            self.repo.db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Username or email already exists",
            ) from exc
        return self.build_token_response(created)

    def login_user(self, payload: UserLoginRequest) -> TokenResponse:
        user = self.repo.get_by_email(str(payload.email).strip().lower())
        if not user or not user.password_hash:
            # 不区分“邮箱不存在”和“密码错误”，避免向外泄露账号是否存在。
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )

        if not self.verify_password(payload.password, user.password_hash):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password",
            )

        return self.build_token_response(user)

    def build_token_response(self, user: User) -> TokenResponse:
        # token 是认证凭证，user 是前端渲染当前用户信息所需的安全字段快照。
        token = self.create_access_token(user)
        return TokenResponse(
            access_token=token,
            token_type="bearer",
            user=UserResponse.model_validate(user),
        )

    def create_access_token(self, user: User) -> str:
        # exp 使用 timezone-aware UTC 时间，避免服务端时区变化影响 token 过期判断。
        # AUTH_SECRET_KEY 变更会让旧 token 全部失效；这是密钥轮换的预期安全边界。
        expire_at = datetime.now(timezone.utc) + timedelta(
            minutes=settings.auth_access_token_expire_minutes
        )
        payload = {
            # sub 是 JWT 标准里的 subject，这里用用户主键作为登录主体。
            "sub": user.id,
            "email": user.email,
            "exp": expire_at,
        }
        return jwt.encode(
            payload,
            settings.auth_secret_key,
            algorithm=settings.auth_algorithm,
        )

    @staticmethod
    def hash_password(password: str) -> str:
        # bcrypt.gensalt() 会为每个密码生成独立 salt，同样密码也会得到不同 hash。
        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    @staticmethod
    def verify_password(password: str, password_hash: str) -> bool:
        # 校验时由 bcrypt 从 hash 中读取 salt 和成本参数，不需要额外保存 salt 字段。
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
