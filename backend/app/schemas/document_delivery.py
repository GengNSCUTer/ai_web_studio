from datetime import datetime

from pydantic import BaseModel, Field


class DocumentDraftCreate(BaseModel):
    file_name: str = Field(min_length=1, max_length=180)
    content: str = Field(min_length=1, max_length=500_000)
    format: str = Field(default="markdown", pattern="^(markdown|docx)$")
    source_references: list[str] = Field(default_factory=list, max_length=100)
    conversation_id: str | None = Field(default=None, max_length=36)


class DocumentDraftResponse(BaseModel):
    file_id: str
    project_id: str
    file_name: str
    format: str
    file_size: int
    content_hash: str
    source_references: list[str]
    download_url: str
    created_at: datetime
