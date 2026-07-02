from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.models.user import User
from app.repositories.user_repo import UserRepository
from app.schemas.auth import TokenResponse, UserLoginRequest, UserRegisterRequest, UserResponse
from app.services.auth_service import AuthService

# Router 层只负责 HTTP 契约和依赖注入；注册、登录的业务规则放在 AuthService。
router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def register(
    payload: UserRegisterRequest,
    db: Session = Depends(get_db),
) -> TokenResponse:
    # 每次请求使用独立 db session，再把数据访问能力注入 service。
    return AuthService(UserRepository(db)).register_user(payload)


@router.post("/login", response_model=TokenResponse)
def login(
    payload: UserLoginRequest,
    db: Session = Depends(get_db),
) -> TokenResponse:
    # Router 不直接校验密码或生成 JWT，避免 HTTP 层混入业务细节。
    return AuthService(UserRepository(db)).login_user(payload)


@router.get("/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)) -> UserResponse:
    # get_current_user 已完成 token 校验和用户查询，这里只做 ORM -> API Schema 转换。
    return UserResponse.model_validate(current_user)
