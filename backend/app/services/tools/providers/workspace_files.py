from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.project_file import ProjectFile
from app.services.tools.schemas import ExternalSource, PlannedToolCall


class WorkspaceFileToolProvider:
    """Constrained, read-only access to project files already owned by the user.

    This provider never accepts an OS path, storage key, or shell expression from
    the model.  The only addressable identifier is a ProjectFile id, and every
    database lookup is scoped by ``user_id`` and, when present, ``project_id``.
    """

    MAX_LIST_RESULTS = 30
    MAX_SEARCH_RESULTS = 5
    MAX_READ_LINES = 200
    MAX_SOURCE_CHARS = 12_000

    def __init__(self, *, db: Session | None, user_id: str | None, project_id: str | None) -> None:
        self.db = db
        self.user_id = user_id
        self.project_id = project_id

    async def run(self, *, call: PlannedToolCall) -> tuple[list[ExternalSource], dict[str, Any]]:
        if not self.db or not self.user_id:
            raise RuntimeError("工作区文件工具缺少用户数据库上下文。")
        if not self.project_id:
            # ProjectFile is a project-scoped resource. Falling back to every
            # file owned by the user would silently widen the workspace boundary.
            raise RuntimeError("工作区文件工具需要关联项目后才能使用。")
        if call.tool_key == "workspace.files.list":
            return self._list_files(call)
        if call.tool_key == "workspace.files.search":
            return self._search_files(call)
        if call.tool_key == "workspace.files.read":
            return self._read_file(call)
        raise RuntimeError(f"未知工作区文件工具：{call.tool_key}")

    def _base_statement(self):
        return select(ProjectFile).where(
            ProjectFile.user_id == self.user_id,
            ProjectFile.project_id == self.project_id,
        )

    def _list_files(self, call: PlannedToolCall) -> tuple[list[ExternalSource], dict[str, Any]]:
        files = list(
            self.db.scalars(self._base_statement().order_by(ProjectFile.created_at.desc()).limit(self.MAX_LIST_RESULTS)).all()
        )
        if not files:
            return [], {"adapter_type": "workspace_file", "operation": "list", "files_count": 0}

        lines = [
            f"- id={item.id}; name={item.file_name}; type={item.mime_type or item.kind}; size={item.file_size or 0}"
            for item in files
        ]
        return (
            [
                ExternalSource(
                    source_type="workspace_file_list",
                    provider="workspace",
                    title="工作区文件列表",
                    display_text="\n".join(lines),
                    metadata={
                        "raw": {
                            "files": [
                                {
                                    "file_id": item.id,
                                    "file_name": item.file_name,
                                    "mime_type": item.mime_type,
                                    "file_size": item.file_size,
                                }
                                for item in files
                            ]
                        }
                    },
                )
            ],
            {"adapter_type": "workspace_file", "operation": "list", "files_count": len(files)},
        )

    def _search_files(self, call: PlannedToolCall) -> tuple[list[ExternalSource], dict[str, Any]]:
        query = str(call.arguments.get("query") or "").strip()
        if not query:
            raise RuntimeError("文件搜索缺少 query。")
        candidates = list(self.db.scalars(self._base_statement().order_by(ProjectFile.created_at.desc()).limit(120)).all())
        query_terms = self._search_terms(query)
        ranked: list[tuple[float, ProjectFile, str]] = []
        for item in candidates:
            text = (item.parsed_text or "").strip()
            haystack = f"{item.file_name}\n{text}"
            score = self._score(query_terms, haystack)
            if score <= 0:
                continue
            ranked.append((score, item, self._snippet(text=text, query_terms=query_terms)))

        ranked.sort(key=lambda entry: (-entry[0], entry[1].file_name, entry[1].id))
        sources: list[ExternalSource] = []
        for score, item, snippet in ranked[: self.MAX_SEARCH_RESULTS]:
            sources.append(
                ExternalSource(
                    source_type="workspace_file_search",
                    provider="workspace",
                    title=item.file_name,
                    display_text=snippet or "文件名匹配，暂无可用文本片段。",
                    score=score,
                    metadata={
                        "file_id": item.id,
                        "mime_type": item.mime_type or item.kind,
                        "raw": {"file_id": item.id, "file_name": item.file_name, "score": score},
                    },
                )
            )
        return sources, {
            "adapter_type": "workspace_file",
            "operation": "search",
            "query_length": len(query),
            "matched_files": len(sources),
        }

    def _read_file(self, call: PlannedToolCall) -> tuple[list[ExternalSource], dict[str, Any]]:
        file_id = str(call.arguments.get("file_id") or "").strip()
        if not file_id:
            raise RuntimeError("读取文件缺少 file_id。")
        item = self.db.scalars(self._base_statement().where(ProjectFile.id == file_id).limit(1)).first()
        if not item:
            # Do not distinguish an absent file from another user's file.
            raise RuntimeError("工作区中未找到该文件。")
        text = (item.parsed_text or "").strip()
        if not text:
            return [], {"adapter_type": "workspace_file", "operation": "read", "file_id": item.id, "empty": True}

        start_line = self._bounded_int(call.arguments.get("start_line"), default=1, lower=1, upper=1_000_000)
        max_lines = self._bounded_int(
            call.arguments.get("max_lines"), default=80, lower=1, upper=self.MAX_READ_LINES
        )
        lines = text.splitlines()
        start_index = min(start_line - 1, len(lines))
        end_index = min(len(lines), start_index + max_lines)
        rendered_lines: list[str] = []
        chars = 0
        for index, line in enumerate(lines[start_index:end_index], start=start_index + 1):
            rendered = f"{index}: {line}"
            if chars + len(rendered) + 1 > self.MAX_SOURCE_CHARS:
                rendered_lines.append("[文件片段已达安全输出上限]")
                break
            rendered_lines.append(rendered)
            chars += len(rendered) + 1
        display_text = "\n".join(rendered_lines) or "请求的行范围为空。"
        return (
            [
                ExternalSource(
                    source_type="workspace_file_read",
                    provider="workspace",
                    title=item.file_name,
                    display_text=display_text,
                    metadata={
                        "file_id": item.id,
                        "mime_type": item.mime_type or item.kind,
                        "line_start": start_index + 1,
                        "line_end": min(end_index, start_index + len(rendered_lines)),
                        "raw": {
                            "file_id": item.id,
                            "file_name": item.file_name,
                            "line_start": start_index + 1,
                            "line_end": min(end_index, start_index + len(rendered_lines)),
                        },
                    },
                )
            ],
            {
                "adapter_type": "workspace_file",
                "operation": "read",
                "file_id": item.id,
                "line_start": start_index + 1,
                "line_end": min(end_index, start_index + len(rendered_lines)),
            },
        )

    @staticmethod
    def _bounded_int(value: Any, *, default: int, lower: int, upper: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return max(lower, min(parsed, upper))

    @staticmethod
    def _search_terms(value: str) -> set[str]:
        normalized = value.lower()
        terms = set(re.findall(r"[a-z0-9_]{2,}", normalized))
        for run in re.findall(r"[\u4e00-\u9fff]+", normalized):
            terms.update(run[index : index + 2] for index in range(max(0, len(run) - 1)))
        return terms

    @staticmethod
    def _score(query_terms: set[str], text: str) -> float:
        if not query_terms:
            return 0.0
        haystack = text.lower()
        matched = [term for term in query_terms if term in haystack]
        if not matched:
            return 0.0
        return round(len(matched) / len(query_terms), 4)

    @staticmethod
    def _snippet(*, text: str, query_terms: set[str]) -> str:
        if not text:
            return ""
        lowered = text.lower()
        positions = [lowered.find(term) for term in query_terms if lowered.find(term) >= 0]
        if not positions:
            return text[:900]
        start = max(0, min(positions) - 240)
        end = min(len(text), start + 1200)
        prefix = "..." if start else ""
        suffix = "..." if end < len(text) else ""
        return f"{prefix}{text[start:end].strip()}{suffix}"
