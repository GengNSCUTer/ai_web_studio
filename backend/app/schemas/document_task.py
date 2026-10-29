"""文档任务只接受项目资料标识，不接受本机路径或可执行脚本。"""

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class DocumentTaskCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str = Field(min_length=1, max_length=36)
    conversation_id: str | None = Field(default=None, max_length=36)
    assistant_message_id: str | None = Field(default=None, max_length=36)
    file_ids: list[str] = Field(default_factory=list, max_length=12)
    instruction: str = Field(default="依据授权资料整理成清晰的文档，保留来源与不确定性。", min_length=1, max_length=2000)
    file_name: str = Field(default="资料整理.md", min_length=1, max_length=160)
    format: Literal["markdown", "docx"] = "markdown"
    idempotency_key: str = Field(min_length=1, max_length=80)
