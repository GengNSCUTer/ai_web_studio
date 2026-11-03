"""只保存资源选择，远端正文在每次读取和注入前重新检查。"""

from pydantic import BaseModel, Field, field_validator


class McpResourceSelection(BaseModel):
    server_id: str = Field(min_length=1, max_length=36)
    uri: str = Field(min_length=1, max_length=2048)

    @field_validator("uri")
    @classmethod
    def no_controls(cls, value):
        if any(ord(c) < 32 for c in value):
            raise ValueError("资源URI不能含控制字符")
        return value
