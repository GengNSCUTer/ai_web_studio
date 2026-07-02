from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserRegisterRequest(BaseModel):
    # Schema 是 API 入参契约：邮箱格式在边界层先校验，业务去重留给 AuthService/数据库约束。
    username: str = Field(min_length=3, max_length=64)
    email: EmailStr = Field(max_length=255)
    password: str = Field(min_length=8, max_length=128)


class UserLoginRequest(BaseModel):
    # 登录只需要稳定身份字段 email 和明文 password；password 不会被持久化。
    email: EmailStr = Field(max_length=255)
    password: str = Field(min_length=8, max_length=128)


class UserResponse(BaseModel):
    # from_attributes=True 允许 Pydantic 直接从 SQLAlchemy ORM 对象读取字段。
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str | None = None
    email: str | None = None
    created_at: datetime


class TokenResponse(BaseModel):
    # 登录/注册成功后返回 token 和当前用户快照，前端据此保存登录态。
    access_token: str
    token_type: str = "bearer"
    user: UserResponse
