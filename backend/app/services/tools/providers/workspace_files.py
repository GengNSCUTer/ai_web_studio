from __future__ import annotations

import asyncio
import difflib
import hashlib
import re
import threading
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.models.project_file import ProjectFile
from app.services.workspace_file_provenance import current_file_provenance
from app.services.tools.schemas import ExternalSource, PlannedToolCall, ToolExecutionFeedbackError
from app.services.tools.providers.workspace_file_paging import WorkspaceFileCursor


class WorkspaceFileToolProvider:
    """在独立线程和会话中访问当前用户、当前项目的文件，不接受本机路径。

    列表和搜索按页获取快照，释放连接后匹配文本；返回来源时短事务复核版本。
    独立会话绝不提交调用者正在准备的 Chat、审批或 Worker 事务。
    """

    MAX_LIST_RESULTS = 30
    MAX_SEARCH_RESULTS = 5
    MAX_SEARCH_FILES = 120
    MAX_READ_LINES = 200
    MAX_SOURCE_CHARS = 12_000
    # 有权访问的文件仍可能包含凭据，不能因为通过项目隔离就把秘密送给模型。
    SENSITIVE_FILE_NAME_PATTERN = re.compile(
        r"(^|/)(\.env(?:\..*)?|.*(?:credentials?|secrets?|id_rsa|\.pem|\.key))$",
        flags=re.IGNORECASE,
    )
    SENSITIVE_CONTENT_PATTERNS = (
        re.compile(
            r"(?i)\b(api[_-]?key|access[_-]?token|password|passwd|secret|authorization)\b\s*[:=]\s*[^\s,;]+"
        ),
        re.compile(r"(?i)\b(?:sk-[a-z0-9_-]{16,}|ghp_[a-z0-9]{20,}|AKIA[0-9A-Z]{16})\b"),
    )

    def __init__(self, *, db: Session | None, user_id: str | None, project_id: str | None,
                 session_factory=None) -> None:
        self.db = db
        self.user_id = user_id
        self.project_id = project_id
        self.session_factory = session_factory or (
            sessionmaker(bind=db.get_bind().engine, autoflush=False, expire_on_commit=False) if db is not None else None
        )
        self._cancelled: threading.Event | None = None

    async def run(self, *, call: PlannedToolCall) -> tuple[list[ExternalSource], dict[str, Any]]:
        if not self.session_factory or not self.user_id:
            raise ToolExecutionFeedbackError("工作区文件工具缺少用户数据库上下文。")
        if not self.project_id:
            # 缺少项目时不能退化成搜索该用户的全部文件，避免扩大工作区边界。
            raise ToolExecutionFeedbackError("工作区文件工具需要关联项目后才能使用。")
        cancelled = threading.Event()
        try:
            return await asyncio.to_thread(self._run_isolated, call, cancelled)
        except asyncio.CancelledError:
            # Python 无法强杀正在查询的线程。查询返回后，线程自行回滚并关闭自己的会话。
            cancelled.set()
            raise

    def _run_isolated(self, call: PlannedToolCall, cancelled: threading.Event):
        with self.session_factory() as db:
            worker = WorkspaceFileToolProvider(db=db, user_id=self.user_id, project_id=self.project_id)
            worker._cancelled = cancelled
            worker._check_cancelled()
            result = worker._run_sync(call)
            worker._check_cancelled()
            # 只提交文件版本基线；文件正文、审批和原始 Chat 事务不在此处写入。
            db.commit()
            return result

    def _check_cancelled(self) -> None:
        if self._cancelled is not None and self._cancelled.is_set():
            raise ToolExecutionFeedbackError("文件操作已取消。")

    def _run_sync(self, call: PlannedToolCall) -> tuple[list[ExternalSource], dict[str, Any]]:
        if call.tool_key == "workspace.files.list":
            return self._list_files(call)
        if call.tool_key == "workspace.files.search":
            return self._search_files(call)
        if call.tool_key == "workspace.files.read":
            return self._read_file(call)
        if call.tool_key == "workspace.files.propose_edit":
            return self._propose_edit(call)
        raise ToolExecutionFeedbackError("未知工作区文件工具。")

    def _base_statement(self):
        return select(ProjectFile).where(
            ProjectFile.user_id == self.user_id,
            ProjectFile.project_id == self.project_id,
        )

    def _page(self, call: PlannedToolCall, *, size: int) -> tuple[list[ProjectFile], dict]:
        statement = self._base_statement()
        file_name = str(call.arguments.get("file_name") or "").strip()
        file_id = str(call.arguments.get("file_id") or "").strip()
        if file_name:
            statement = statement.where(ProjectFile.file_name == file_name)
        if file_id:
            statement = statement.where(ProjectFile.id == file_id)
        scope = WorkspaceFileCursor.scope(user_id=self.user_id, project_id=self.project_id,
                                          operation=call.tool_key, arguments=call.arguments)
        cursor = call.arguments.get("cursor")
        if cursor:
            created_at, previous_id = WorkspaceFileCursor.decode(cursor, scope=scope)
            statement = statement.where(or_(ProjectFile.created_at < created_at,
                and_(ProjectFile.created_at == created_at, ProjectFile.id < previous_id)))
        rows = list(self.db.scalars(statement.order_by(ProjectFile.created_at.desc(), ProjectFile.id.desc())
                                    .limit(size + 1)).all())
        page = rows[:size]
        has_more = len(rows) > size
        next_cursor = WorkspaceFileCursor.encode(created_at=page[-1].created_at, file_id=page[-1].id,
                                                 scope=scope) if has_more else None
        files = [row for row in page if not self.is_sensitive_file_name(row.file_name)]
        # 文本快照脱离 ORM 后再计算匹配分数，不在 CPU 搜索期间占用连接或行锁。
        self.db.expunge_all()
        self.db.rollback()
        return files, {"page_id": hashlib.sha256(f"{scope}:{cursor or 'first'}".encode()).hexdigest()[:16],
                       "scanned_files": len(files), "has_more": has_more, "next_cursor": next_cursor,
                       "coverage_complete": not has_more, "scope_kind": "exact_file" if file_name or file_id else "project_page"}

    @staticmethod
    def _page_notice(page: dict) -> str:
        matches_notice = (f" 本页快照匹配 {page['matched_files_total']} 个文件，仅返回最相关的前 {WorkspaceFileToolProvider.MAX_SEARCH_RESULTS} 个。"
                          if page.get("results_truncated") else "")
        if page["has_more"]:
            return f"本页检查 {page['scanned_files']} 个可访问文件；还有更早文件未检查，可用返回的 cursor 继续查询。" + matches_notice
        return f"本页检查 {page['scanned_files']} 个可访问文件，已到当前查询范围的最后一页。" + matches_notice

    def _refresh_provenance(self, files: list[ProjectFile]) -> dict[str, tuple[ProjectFile, dict]]:
        records = {}
        # 统一锁顺序避免不同列表/搜索同时补建基线时互相死锁。
        for item in sorted(files, key=lambda row: row.id):
            self._check_cancelled()
            current = self.db.scalars(self._base_statement().where(ProjectFile.id == item.id)
                                      .with_for_update()).first()
            if current is None or self.is_sensitive_file_name(current.file_name):
                continue
            records[current.id] = (current, current_file_provenance(db=self.db, project_file=current))
        return records

    def _list_files(self, call: PlannedToolCall) -> tuple[list[ExternalSource], dict[str, Any]]:
        files, page = self._page(call, size=self.MAX_LIST_RESULTS)
        refreshed = self._refresh_provenance(files)
        files = [refreshed[item.id][0] for item in files if item.id in refreshed]
        if not files:
            return (
                [
                    ExternalSource(
                        source_type="workspace_file_list",
                        provider="workspace",
                        title="工作区文件列表",
                        display_text=("当前已检查范围没有可供 Agent 访问的文件。" if page["has_more"]
                                      else "当前查询范围没有可供 Agent 访问的文件。") + "\n" + self._page_notice(page),
                        metadata={
                            **page,
                            "empty_reason": "no_accessible_files",
                            "result_semantics": "empty_answer",
                            "raw": {"files": []},
                        },
                    )
                ],
                {
                    "adapter_type": "workspace_file",
                    "operation": "list",
                    "files_count": 0,
                    **page,
                    "result_semantics": "empty_answer",
                },
            )

        file_records = [
            {
                **refreshed[item.id][1],
                "file_name": item.file_name,
                "mime_type": item.mime_type or item.kind,
                "file_size": item.file_size,
            }
            for item in files
        ]
        lines = [
            f"- id={record['file_id']}; name={record['file_name']}; "
            f"type={record['mime_type']}; size={record['file_size']}"
            for record in file_records
        ]
        return (
            [
                ExternalSource(
                    source_type="workspace_file_list",
                    provider="workspace",
                    title="工作区文件列表",
                    display_text=self._page_notice(page) + "\n" + "\n".join(lines),
                    metadata={
                        **page,
                        "raw": {
                            "files": file_records
                        }
                    },
                )
            ],
            {"adapter_type": "workspace_file", "operation": "list", "files_count": len(files), **page},
        )

    def _search_files(self, call: PlannedToolCall) -> tuple[list[ExternalSource], dict[str, Any]]:
        query = str(call.arguments.get("query") or "").strip()
        if not query:
            raise ToolExecutionFeedbackError("文件搜索缺少 query。")
        candidates, page = self._page(call, size=self.MAX_SEARCH_FILES)
        query_terms = self._search_terms(query)
        ranked: list[tuple[float, ProjectFile]] = []
        for item in candidates:
            self._check_cancelled()
            text = (item.parsed_text or "").strip()
            haystack = f"{item.file_name}\n{text}"
            score = self._score(query_terms, haystack)
            if score <= 0:
                continue
            ranked.append((score, item))

        ranked.sort(key=lambda entry: (-entry[0], entry[1].file_name, entry[1].id))
        page["matched_files_total"] = len(ranked)
        page["results_truncated"] = len(ranked) > self.MAX_SEARCH_RESULTS
        selected = ranked[:self.MAX_SEARCH_RESULTS]
        refreshed = self._refresh_provenance([item for _, item in selected])
        sources: list[ExternalSource] = []
        for _, item in selected:
            if item.id not in refreshed:
                continue
            item, provenance = refreshed[item.id]
            # 快照匹配后文件可能被修改；返回的摘要和版本必须来自同一份当前正文。
            text = (item.parsed_text or "").strip()
            score = self._score(query_terms, f"{item.file_name}\n{text}")
            if score <= 0:
                continue
            snippet = self._redact_text(self._snippet(text=text, query_terms=query_terms))
            sources.append(
                ExternalSource(
                    source_type="workspace_file_search",
                    provider="workspace",
                    title=item.file_name,
                    display_text=self._page_notice(page) + "\n" + (snippet or "文件名匹配，暂无可用文本片段。"),
                    score=score,
                    metadata={
                        **provenance,
                        **page,
                        "mime_type": item.mime_type or item.kind,
                        "raw": {**provenance, "file_name": item.file_name, "score": score},
                    },
                )
            )
        if not sources:
            return (
                [
                    ExternalSource(
                        source_type="workspace_file_search",
                        provider="workspace",
                        title="工作区文件搜索",
                        display_text="当前已检查范围未找到与本次查询匹配的文件。\n" + self._page_notice(page),
                        metadata={
                            **page,
                            "empty_reason": "no_matching_files",
                            "result_semantics": "empty_answer",
                            "raw": {"matches": []},
                        },
                    )
                ],
                {
                    "adapter_type": "workspace_file",
                    "operation": "search",
                    "query_length": len(query),
                    "matched_files": 0,
                    **page,
                    "result_semantics": "empty_answer",
                },
            )
        return sources, {
            "adapter_type": "workspace_file",
            "operation": "search",
            "query_length": len(query),
            "matched_files": len(sources),
            **page,
        }

    def _read_file(self, call: PlannedToolCall) -> tuple[list[ExternalSource], dict[str, Any]]:
        file_id = str(call.arguments.get("file_id") or "").strip()
        if not file_id:
            raise ToolExecutionFeedbackError("读取文件缺少 file_id。")
        item = self.db.scalars(self._base_statement().where(ProjectFile.id == file_id).with_for_update()).first()
        if not item:
            # 不区分文件不存在与无权访问，避免暴露其他用户或项目的文件。
            raise ToolExecutionFeedbackError("工作区中未找到该文件。")
        self._ensure_agent_file_allowed(item.file_name)
        provenance = current_file_provenance(db=self.db, project_file=item)
        self._ensure_expected_revision(
            expected_revision_id=call.arguments.get("expected_revision_id"),
            provenance=provenance,
        )
        text = (item.parsed_text or "").strip()
        if not text:
            return (
                [
                    ExternalSource(
                        source_type="workspace_file_read",
                        provider="workspace",
                        title=item.file_name,
                        display_text="目标文件存在，但没有可读取的解析文本。",
                    metadata={
                            **provenance,
                            "mime_type": item.mime_type or item.kind,
                            "empty_reason": "parsed_text_empty",
                            "result_semantics": "empty_answer",
                            "raw": {**provenance, "content": ""},
                        },
                    )
                ],
                {
                    "adapter_type": "workspace_file",
                    "operation": "read",
                    "file_id": item.id,
                    "revision_id": provenance["revision_id"],
                    "empty": True,
                    "result_semantics": "empty_answer",
                },
            )

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
            rendered = f"{index}: {self._redact_text(line)}"
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
                        **provenance,
                        "mime_type": item.mime_type or item.kind,
                        "line_start": start_index + 1,
                        "line_end": min(end_index, start_index + len(rendered_lines)),
                        "raw": {
                            **provenance,
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
                "revision_id": provenance["revision_id"],
                "line_start": start_index + 1,
                "line_end": min(end_index, start_index + len(rendered_lines)),
            },
        )

    def _propose_edit(self, call: PlannedToolCall) -> tuple[list[ExternalSource], dict[str, Any]]:
        """验证唯一替换并生成 Diff，不修改文件正文。"""

        file_id = str(call.arguments.get("file_id") or "").strip()
        old_string = str(call.arguments.get("old_string") or "")
        new_string = str(call.arguments.get("new_string") or "")
        if not file_id:
            raise ToolExecutionFeedbackError("编辑预览缺少 file_id。")
        if not old_string:
            raise ToolExecutionFeedbackError("编辑预览的 old_string 不能为空。")
        item = self.db.scalars(self._base_statement().where(ProjectFile.id == file_id).with_for_update()).first()
        if not item:
            raise ToolExecutionFeedbackError("工作区中未找到该文件。")
        self._ensure_agent_file_allowed(item.file_name)

        provenance = current_file_provenance(db=self.db, project_file=item)
        self._ensure_expected_revision(
            expected_revision_id=call.arguments.get("expected_revision_id"),
            provenance=provenance,
        )

        original = item.parsed_text or ""
        matches = original.count(old_string)
        if matches == 0:
            raise ToolExecutionFeedbackError("old_string 未在当前文件版本中找到；请先重新读取相关行。")
        if matches > 1:
            raise ToolExecutionFeedbackError(
                f"old_string 在当前文件版本中出现 {matches} 次；请提供更多上下文使其唯一。"
            )

        updated = original.replace(old_string, new_string, 1)
        diff_lines = list(
            difflib.unified_diff(
                original.splitlines(),
                updated.splitlines(),
                fromfile=f"a/{item.file_name}",
                tofile=f"b/{item.file_name}",
                lineterm="",
                n=3,
            )
        )
        diff_text = self._redact_text("\n".join(diff_lines))
        if len(diff_text) > self.MAX_SOURCE_CHARS:
            diff_text = diff_text[: self.MAX_SOURCE_CHARS].rstrip() + "\n[Diff 已达安全输出上限]"
        start_line = original[: original.index(old_string)].count("\n") + 1
        end_line = start_line + old_string.count("\n")
        source = ExternalSource(
            source_type="workspace_file_edit_preview",
            provider="workspace",
            title=f"{item.file_name} 编辑预览（尚未写入）",
            display_text=(
                "以下内容只是经过服务端唯一匹配校验的 Diff 预览，未修改源文件：\n"
                f"{diff_text or '[替换后文本无可见差异]'}"
            ),
            metadata={
                **provenance,
                "mime_type": item.mime_type or item.kind,
                "line_start": start_line,
                "line_end": end_line,
                "raw": {
                    **provenance,
                    "line_start": start_line,
                    "line_end": end_line,
                    "applied": False,
                },
            },
        )
        return [source], {
            "adapter_type": "workspace_file",
            "operation": "propose_edit",
            "file_id": item.id,
            "revision_id": provenance["revision_id"],
            "line_start": start_line,
            "line_end": end_line,
            "applied": False,
            "result_semantics": "approval_draft",
        }

    @staticmethod
    def _bounded_int(value: Any, *, default: int, lower: int, upper: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return max(lower, min(parsed, upper))

    @staticmethod
    def _ensure_expected_revision(
        *, expected_revision_id: Any,
        provenance: dict[str, str | int],
    ) -> None:
        expected = str(expected_revision_id or "").strip()
        if expected and expected != provenance["revision_id"]:
            raise ToolExecutionFeedbackError("文件版本已变化，请重新读取当前内容后再继续。")

    @classmethod
    def is_sensitive_file_name(cls, file_name: str | None) -> bool:
        return bool(cls.SENSITIVE_FILE_NAME_PATTERN.search((file_name or "").strip()))

    @classmethod
    def _ensure_agent_file_allowed(cls, file_name: str | None) -> None:
        if cls.is_sensitive_file_name(file_name):
            raise ToolExecutionFeedbackError("出于安全原因，该敏感文件不允许通过 Agent 工具读取或修改。")

    @classmethod
    def _redact_text(cls, text: str) -> str:
        redacted = text
        for pattern in cls.SENSITIVE_CONTENT_PATTERNS:
            redacted = pattern.sub(
                lambda match: f"{match.group(1) if match.lastindex else '敏感值'}=***",
                redacted,
            )
        return redacted

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
