from __future__ import annotations

from dataclasses import dataclass
import io
from pathlib import Path
import time
from zipfile import ZipFile

import requests

from app.core.config import settings
from app.models.knowledge import KnowledgeDocument
from app.services.file_parser_service import FileParserService
from app.services.tools.credentials import ToolCredentialResolver


@dataclass
class ParseResult:
    markdown_path: str
    markdown_preview: str


class KnowledgeParserService:
    """Document parser facade for knowledge-base ingestion."""

    MAX_MARKDOWN_PREVIEW_CHARS = 24000
    MINERU_API_BASE = "https://mineru.net/api/v4"
    MINERU_DONE_STATES = {"done"}
    MINERU_FAILED_STATES = {"failed"}
    MINERU_PENDING_STATES = {"waiting-file", "pending", "running", "converting"}

    def __init__(self, credential_resolver: ToolCredentialResolver | None = None):
        self.credential_resolver = credential_resolver
        self.local_parser = FileParserService()

    def parse(self, *, document: KnowledgeDocument, user_id: str) -> ParseResult:
        if document.parser_provider == "mineru":
            return self._parse_with_mineru(document=document, user_id=user_id)
        return self._parse_local(document=document, user_id=user_id)

    def _parse_local(self, *, document: KnowledgeDocument, user_id: str) -> ParseResult:
        file_path = self._resolve_upload_path(document.storage_key, user_id)
        parsed_text = self.local_parser.parse_file(file_path)
        if not parsed_text:
            raise RuntimeError("本地解析未提取到有效文本。")
        markdown = self._to_markdown(document.file_name, parsed_text)
        return self._save_markdown(document=document, user_id=user_id, markdown=markdown)

    def _parse_with_mineru(self, *, document: KnowledgeDocument, user_id: str) -> ParseResult:
        if not self.credential_resolver:
            raise RuntimeError("MinerU 凭据解析器未初始化。")
        credential = self.credential_resolver.resolve(user_id=user_id, provider_key="mineru")
        if not credential.is_enabled or not credential.api_key:
            raise RuntimeError("MinerU 未配置或未启用，请先在知识库页面配置 MinerU token。")
        file_path = self._resolve_upload_path(document.storage_key, user_id)
        markdown = self._parse_with_mineru_precision_api(
            file_path=file_path,
            file_name=document.file_name,
            token=credential.api_key,
            data_id=document.id,
        )
        if not markdown.strip():
            raise RuntimeError("MinerU 解析完成，但未提取到 Markdown 内容。")
        return self._save_markdown(document=document, user_id=user_id, markdown=markdown)

    def test_mineru_connection(self, *, user_id: str) -> tuple[bool, str]:
        if not self.credential_resolver:
            return False, "MinerU 凭据解析器未初始化。"
        credential = self.credential_resolver.resolve(user_id=user_id, provider_key="mineru")
        if not credential.is_enabled or not credential.api_key:
            return False, "MinerU 未配置或未启用。"
        if len(credential.api_key.strip()) < 20:
            return False, "MinerU token 长度异常，请检查后重试。"
        return True, f"MinerU token 已配置，凭据来源：{credential.source}；真实解析会在文档解析时调用 MinerU 精准 API。"

    def preview_markdown(self, *, markdown_path: str, user_id: str) -> str:
        path = self._resolve_markdown_path(markdown_path, user_id)
        return path.read_text(encoding="utf-8", errors="ignore")[: self.MAX_MARKDOWN_PREVIEW_CHARS]

    def _save_markdown(self, *, document: KnowledgeDocument, user_id: str, markdown: str) -> ParseResult:
        user_root = Path(settings.upload_dir) / user_id / "knowledge" / document.knowledge_base_id
        user_root.mkdir(parents=True, exist_ok=True)
        markdown_name = f"{document.id}.md"
        markdown_path = user_root / markdown_name
        markdown_path.write_text(markdown, encoding="utf-8")
        relative_path = f"{user_id}/knowledge/{document.knowledge_base_id}/{markdown_name}"
        return ParseResult(
            markdown_path=relative_path,
            markdown_preview=markdown[: self.MAX_MARKDOWN_PREVIEW_CHARS],
        )

    @staticmethod
    def _to_markdown(file_name: str, parsed_text: str) -> str:
        title = Path(file_name).stem.strip() or "Untitled"
        return f"# {title}\n\n{parsed_text.strip()}\n"

    def _parse_with_mineru_precision_api(
        self,
        *,
        file_path: Path,
        file_name: str,
        token: str,
        data_id: str,
    ) -> str:
        upload_info = self._request_mineru_upload_url(file_name=file_name, token=token, data_id=data_id)
        batch_id = str(upload_info["batch_id"])
        file_url = str(upload_info["file_url"])
        self._upload_file_to_mineru(file_path=file_path, upload_url=file_url)
        zip_url = self._wait_mineru_result(batch_id=batch_id, token=token)
        return self._download_mineru_markdown(zip_url=zip_url)

    def _request_mineru_upload_url(self, *, file_name: str, token: str, data_id: str) -> dict[str, str]:
        response = requests.post(
            f"{self.MINERU_API_BASE}/file-urls/batch",
            headers=self._mineru_headers(token),
            json={
                "enable_formula": True,
                "enable_table": True,
                "language": "ch",
                "model_version": "vlm",
                "files": [{"name": file_name, "data_id": data_id}],
            },
            timeout=30,
        )
        payload = self._mineru_json(response)
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise RuntimeError("MinerU 上传 URL 响应缺少 data 字段。")
        batch_id = data.get("batch_id")
        file_urls = data.get("file_urls")
        if not batch_id or not isinstance(file_urls, list) or not file_urls:
            raise RuntimeError("MinerU 上传 URL 响应缺少 batch_id 或 file_urls。")
        first_file = file_urls[0]
        if not isinstance(first_file, str) or not first_file:
            raise RuntimeError("MinerU 上传 URL 响应中的 file_url 无效。")
        return {"batch_id": str(batch_id), "file_url": first_file}

    @staticmethod
    def _upload_file_to_mineru(*, file_path: Path, upload_url: str) -> None:
        with file_path.open("rb") as file_obj:
            response = requests.put(upload_url, data=file_obj, timeout=120)
        if response.status_code >= 400:
            raise RuntimeError(f"MinerU 文件上传失败：HTTP {response.status_code}")

    def _wait_mineru_result(self, *, batch_id: str, token: str) -> str:
        started_at = time.monotonic()
        while True:
            response = requests.get(
                f"{self.MINERU_API_BASE}/extract-results/batch/{batch_id}",
                headers=self._mineru_headers(token),
                timeout=30,
            )
            payload = self._mineru_json(response)
            result = self._pick_mineru_result(payload)
            state = str(result.get("state") or "").strip()
            if state in self.MINERU_DONE_STATES:
                full_zip_url = result.get("full_zip_url")
                if not isinstance(full_zip_url, str) or not full_zip_url:
                    raise RuntimeError("MinerU 解析完成，但结果缺少 full_zip_url。")
                return full_zip_url
            if state in self.MINERU_FAILED_STATES:
                error_message = result.get("err_msg") or result.get("message") or "未知错误"
                raise RuntimeError(f"MinerU 解析失败：{error_message}")
            if state not in self.MINERU_PENDING_STATES:
                raise RuntimeError(f"MinerU 返回未知解析状态：{state or 'empty'}")
            if time.monotonic() - started_at > settings.mineru_poll_timeout_seconds:
                raise RuntimeError("MinerU 解析超时，请稍后重试或检查文档大小。")
            time.sleep(settings.mineru_poll_interval_seconds)

    @staticmethod
    def _download_mineru_markdown(*, zip_url: str) -> str:
        response = requests.get(zip_url, timeout=120)
        if response.status_code >= 400:
            raise RuntimeError(f"MinerU 结果下载失败：HTTP {response.status_code}")
        with ZipFile(io.BytesIO(response.content)) as archive:
            markdown_names = [
                name
                for name in archive.namelist()
                if name.lower().endswith(".md") and not name.endswith("/")
            ]
            if not markdown_names:
                raise RuntimeError("MinerU 结果压缩包中没有 Markdown 文件。")
            preferred = next((name for name in markdown_names if name.lower().endswith("full.md")), markdown_names[0])
            return archive.read(preferred).decode("utf-8", errors="ignore")

    @staticmethod
    def _mineru_headers(token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token.strip()}",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _mineru_json(response: requests.Response) -> dict:
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(f"MinerU 响应不是合法 JSON：HTTP {response.status_code}") from exc
        if response.status_code >= 400:
            raise RuntimeError(f"MinerU 请求失败：HTTP {response.status_code}，{payload}")
        code = payload.get("code")
        if code not in (0, "0", None):
            message = payload.get("msg") or payload.get("message") or payload.get("error") or payload
            raise RuntimeError(f"MinerU 请求失败：{message}")
        return payload

    @staticmethod
    def _pick_mineru_result(payload: dict) -> dict:
        data = payload.get("data")
        if isinstance(data, dict):
            extract_result = data.get("extract_result")
            if isinstance(extract_result, list) and extract_result:
                first = extract_result[0]
                if isinstance(first, dict):
                    return first
            if isinstance(extract_result, dict):
                return extract_result
        raise RuntimeError("MinerU 解析结果响应缺少 extract_result。")

    @staticmethod
    def _resolve_upload_path(storage_key: str, user_id: str) -> Path:
        normalized_key = storage_key.strip().replace("\\", "/")
        if not normalized_key.startswith(f"{user_id}/"):
            raise RuntimeError("文档存储路径不属于当前用户。")
        _, _, relative_name = normalized_key.partition("/")
        user_dir = (Path(settings.upload_dir) / user_id).resolve()
        file_path = (user_dir / relative_name).resolve()
        if user_dir not in file_path.parents and file_path != user_dir:
            raise RuntimeError("文档存储路径非法。")
        if not file_path.exists() or not file_path.is_file():
            raise RuntimeError("原始文件不存在，可能已被删除。")
        return file_path

    @staticmethod
    def _resolve_markdown_path(markdown_path: str, user_id: str) -> Path:
        normalized_key = markdown_path.strip().replace("\\", "/")
        if not normalized_key.startswith(f"{user_id}/"):
            raise RuntimeError("Markdown 路径不属于当前用户。")
        _, _, relative_name = normalized_key.partition("/")
        user_dir = (Path(settings.upload_dir) / user_id).resolve()
        file_path = (user_dir / relative_name).resolve()
        if user_dir not in file_path.parents and file_path != user_dir:
            raise RuntimeError("Markdown 路径非法。")
        if not file_path.exists() or not file_path.is_file():
            raise RuntimeError("Markdown 文件不存在。")
        return file_path
