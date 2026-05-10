from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import HTTPException, status

from app.core.config import settings
from app.models.user import User
from app.repositories.user_repo import UserRepository
from app.schemas.auth import TokenResponse, UserLoginRequest, UserRegisterRequest, UserResponse


class AuthService:
    def __init__(self, repo: UserRepository):
        self.repo = repo

    def register_user(self, payload: UserRegisterRequest) -> TokenResponse:
        existing_user = self.repo.find_existing_identity(
            email=payload.email,
            username=payload.username,
        )
        if existing_user:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Username or email already exists",
            )

        user = User(
            username=payload.username.strip(),
            email=payload.email.strip().lower(),
            password_hash=self.hash_password(payload.password),
        )
        created = self.repo.create(user)
        return self.build_token_response(created)

    def login_user(self, payload: UserLoginRequest) -> TokenResponse:
        user = self.repo.get_by_email(payload.email.strip().lower())
        if not user or not user.password_hash:
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
        token = self.create_access_token(user)
        return TokenResponse(
            access_token=token,
            token_type="bearer",
            user=UserResponse.model_validate(user),
        )

    def create_access_token(self, user: User) -> str:
        expire_at = datetime.now(timezone.utc) + timedelta(
            minutes=settings.auth_access_token_expire_minutes
        )
        payload = {
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
        return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

    @staticmethod
    def verify_password(password: str, password_hash: str) -> bool:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
