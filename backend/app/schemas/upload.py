from pydantic import BaseModel, ConfigDict


class UploadItemResponse(BaseModel):
    id: str
    file_name: str
    mime_type: str | None = None
    file_size: int
    kind: str
    storage_key: str
    parsed_text: str | None = None


class UploadItemReference(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    file_name: str
    mime_type: str | None = None
    file_size: int | None = None
    kind: str
    storage_key: str
    parsed_text: str | None = None
