"""验证真实服务的就绪、附件解析、批量回滚和认证 Cookie；不调用本地模型。"""

import io
import json
import sys
import uuid
from pathlib import Path

import httpx
from docx import Document
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.config import settings
from app.core.database import SessionLocal
from app.models.user import User
from app.repositories.user_repo import UserRepository
from app.services.auth_service import AuthService


def main() -> None:
    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "1528713326@qq.com"))
        if not user:
            raise RuntimeError("固定测试账号不存在")
        token = AuthService(UserRepository(db)).create_access_token(user)
        user_id = user.id
    created_paths = []
    user_dir = Path(settings.upload_dir) / user_id
    try:
        with httpx.Client(base_url="http://127.0.0.1:32007", timeout=30, trust_env=False,
                          headers={"Authorization": f"Bearer {token}"}) as client:
            ready = client.get("/health/ready")
            assert ready.status_code == 200, "真实依赖尚未就绪"
            document = Document()
            marker = f"readiness-smoke-{uuid.uuid4().hex}"
            document.add_paragraph(marker)
            content = io.BytesIO()
            document.save(content)
            response = client.post("/api/uploads", files=[
                ("files", ("readiness.md", marker.encode(), "text/markdown")),
                ("files", ("readiness.docx", content.getvalue(),
                           "application/vnd.openxmlformats-officedocument.wordprocessingml.document")),
            ])
            assert response.status_code == 200, f"真实上传失败：HTTP {response.status_code}"
            uploaded = response.json()
            created_paths = [Path(settings.upload_dir) / item["storage_key"] for item in uploaded]
            assert len(uploaded) == 2 and all(marker in item["parsed_text"] for item in uploaded)
            download = client.get("/api/uploads/file", params={"storage_key": uploaded[0]["storage_key"]})
            assert download.content.decode() == marker
            before = set(user_dir.iterdir())
            rejected = client.post("/api/uploads", files=[
                ("files", ("good.md", b"valid text", "text/markdown")),
                ("files", ("empty.md", b" ", "text/markdown")),
            ])
            assert rejected.status_code == 400, "空文档未被拒绝"
            assert set(user_dir.iterdir()) == before, "失败批次留下了半成品文件"
        with httpx.Client(base_url="http://127.0.0.1:32008", timeout=30, trust_env=False) as frontend:
            response = frontend.post("/api/session/logout")
            cookie = response.headers.get("set-cookie", "").lower()
            assert response.status_code == 200
            assert "secure" in cookie and "httponly" in cookie and "max-age=0" in cookie
        print(json.dumps({"readiness": "ready", "real_upload_formats": ["md", "docx"],
                          "download": "ok", "batch_rollback": "ok", "production_cookie": "secure"}))
    finally:
        for path in created_paths:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
