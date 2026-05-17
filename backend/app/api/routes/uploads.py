import mimetypes
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse

from app.api.deps import get_current_user, get_db
from app.core.config import settings
from app.models.user import User
from app.services.file_parser_service import FileParserService
from app.schemas.upload import UploadItemResponse

router = APIRouter(prefix="/uploads", tags=["uploads"])
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_FILE_BYTES = 20 * 1024 * 1024
SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
SUPPORTED_TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".pdf", ".docx"}
SUPPORTED_TEXT_MIME_TYPES = {
    "application/pdf",
    "text/plain",
    "text/markdown",
    "text/x-markdown",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


@dataclass(frozen=True)
class PendingUpload:
    id: str
    original_name: str
    stored_name: str
    target_path: Path
    content: bytes
    mime_type: str
    kind: str


def _classify_upload(*, original_name: str, mime_type: str) -> str | None:
    suffix = Path(original_name).suffix.lower()
    if mime_type.startswith("image/") or suffix in SUPPORTED_IMAGE_EXTENSIONS:
        return "image"
    if suffix in SUPPORTED_TEXT_EXTENSIONS or mime_type in SUPPORTED_TEXT_MIME_TYPES:
        return "file"
    return None


@router.post("", response_model=list[UploadItemResponse])
async def upload_files(
    files: list[UploadFile] = File(...),
    current_user: User = Depends(get_current_user),
    _db=Depends(get_db),
) -> list[UploadItemResponse]:
    user_dir = Path(settings.upload_dir) / current_user.id
    user_dir.mkdir(parents=True, exist_ok=True)

    uploaded_items: list[UploadItemResponse] = []
    parser = FileParserService()
    pending_uploads: list[PendingUpload] = []

    for file in files:
        original_name = Path(file.filename or "upload.bin").name
        suffix = Path(original_name).suffix
        generated_id = str(uuid4())
        stored_name = f"{generated_id}{suffix}"
        target_path = user_dir / stored_name
        content = await file.read()
        mime_type = file.content_type or mimetypes.guess_type(original_name)[0] or "application/octet-stream"
        kind = _classify_upload(original_name=original_name, mime_type=mime_type)
        if not kind:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="当前仅支持图片（png/jpg/jpeg/webp/gif）以及 txt、md、pdf、docx 文档上传",
            )
        if kind == "image" and len(content) > MAX_IMAGE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"图片过大，单文件不能超过 {MAX_IMAGE_BYTES // (1024 * 1024)}MB",
            )
        if kind == "file" and len(content) > MAX_FILE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"文档过大，单文件不能超过 {MAX_FILE_BYTES // (1024 * 1024)}MB",
            )
        pending_uploads.append(
            PendingUpload(
                id=generated_id,
                original_name=original_name,
                stored_name=stored_name,
                target_path=target_path,
                content=content,
                mime_type=mime_type,
                kind=kind,
            )
        )

    written_paths: list[Path] = []
    try:
        for item in pending_uploads:
            item.target_path.write_bytes(item.content)
            written_paths.append(item.target_path)

            parsed_text = parser.parse_file(item.target_path)
            if item.kind == "file" and not parsed_text:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"文档解析失败：{item.original_name} 未提取到有效文本，请检查文件内容后重试",
                )
            uploaded_items.append(
                UploadItemResponse(
                    id=item.id,
                    file_name=item.original_name,
                    mime_type=item.mime_type,
                    file_size=len(item.content),
                    kind=item.kind,
                    storage_key=f"{current_user.id}/{item.stored_name}",
                    parsed_text=parsed_text,
                )
            )
    except Exception:
        for path in written_paths:
            path.unlink(missing_ok=True)
        raise

    return uploaded_items


@router.get("/file")
def get_uploaded_file(
    storage_key: str = Query(..., min_length=1),
    current_user: User = Depends(get_current_user),
) -> FileResponse:
    normalized_key = storage_key.strip().replace("\\", "/")
    if not normalized_key.startswith(f"{current_user.id}/"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    _, _, relative_name = normalized_key.partition("/")
    user_dir = (Path(settings.upload_dir) / current_user.id).resolve()
    file_path = (user_dir / relative_name).resolve()
    if user_dir not in file_path.parents and file_path != user_dir:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    if not file_path.exists() or not file_path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    media_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    return FileResponse(
        path=file_path,
        media_type=media_type,
        filename=file_path.name,
    )
