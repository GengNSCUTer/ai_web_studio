"""客户端只能选择服务器审核的包，不能上传审核状态或任意执行命令。"""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator
from urllib.parse import urlsplit


class ProjectPluginUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(min_length=1, max_length=36)
    enabled: bool
    expected_release_id: str = Field(min_length=1, max_length=36)


class PluginInstallConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: str = Field(pattern="^[a-f0-9]{64}$")


class SubmissionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=128)
    kind: Literal["skill", "mcp", "script"]
    source_url: str = Field(max_length=2048)

    @field_validator("source_url")
    @classmethod
    def source_is_reference_only(cls, value):
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("来源必须是不含凭据的 HTTPS 地址，仅登记，不自动请求。")
        return value


class SandboxPreviewCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(min_length=1, max_length=36)
    plugin_key: str = Field(max_length=128)
    script_key: str = Field(max_length=64)
    file_ids: list[str] = Field(min_length=1, max_length=12)
    domains: list[str] = Field(default_factory=list, max_length=8)
    dependencies: list[str] = Field(default_factory=list, max_length=12)


class SandboxConfirm(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approved: Literal[True]
