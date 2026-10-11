"""公共资料 MCP 的请求约束与文本证据归一化。

同一解析器处理带 URL/Source 标记的网页和文档，不把未知 JSON、错误提示
或整段远端返回直接当作已验证证据。它只整理来源，不赋予内容执行权限。
"""

from __future__ import annotations

import ipaddress
import re
from typing import Any
from urllib.parse import urldefrag, urlsplit

from app.services.tools.schemas import ExternalSource, ToolExecutionFeedbackError, redact_sensitive_text


MAX_SOURCE_CHARS = 8_000
MAX_TOTAL_CHARS = 24_000
MAX_SOURCES = 5
_SOURCE_MARKER = re.compile(r"^(?:URL|Source):[ \t]+(https?://\S+)[ \t]*$", re.MULTILINE)
_HEADING = re.compile(r"^(?:Title:[ \t]*|#{1,3}[ \t]+)(.+)$", re.MULTILINE)
_NEXT_SOURCE_HEADING = re.compile(r"^(?:Title:[ \t]*|#{1,3}[ \t]+).+\n[ \t]*\n?\Z", re.MULTILINE)
_LIBRARY_ID = re.compile(r"^[ \t]*-[ \t]+Context7-compatible library ID:[ \t]*(/[^\s]+)[ \t]*$", re.MULTILINE)
_ERROR_BODY = re.compile(r"^(?:error:|failed to (?:fetch|retrieve)|unable to (?:fetch|retrieve)|rate limit|no (?:results|content|documents) found)", re.IGNORECASE)


def public_url(value: Any) -> str | None:
    """校验公开 HTTP(S) URL 的语法与字面地址，不在解析结果时访问网络。"""

    if not isinstance(value, str) or len(value) > 2_048 or any(ord(char) <= 32 for char in value):
        return None
    if redact_sensitive_text(value) != value:
        return None
    try:
        parsed = urlsplit(value)
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if parsed.scheme not in {"http", "https"} or not hostname:
            return None
        if parsed.username is not None or parsed.password is not None or parsed.port not in {None, 80, 443}:
            return None
        if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")) or "." not in hostname:
            return None
        try:
            if not ipaddress.ip_address(hostname).is_global:
                return None
        except ValueError:
            pass
    except ValueError:
        return None
    return value


def validate_public_lookup_arguments(arguments: dict[str, Any], *, url_fields: list[str]) -> list[str]:
    """拒绝显式凭据和非公开 URL；返回调用前还需检查 DNS 的目标。"""

    values = list(arguments.values())
    while values:
        value = values.pop()
        if isinstance(value, dict):
            values.extend(value.values())
        elif isinstance(value, list):
            values.extend(value)
        elif isinstance(value, str) and redact_sensitive_text(value) != value:
            raise ToolExecutionFeedbackError("公共资料工具不能接收含明显凭据的查询或 URL。")
    urls: list[str] = []
    for field in url_fields:
        items = arguments.get(field)
        if not isinstance(items, list) or not items or len(items) > 3:
            raise ToolExecutionFeedbackError("网页读取需要 1 至 3 个公开 URL。")
        for item in items:
            safe = public_url(item)
            if not safe:
                raise ToolExecutionFeedbackError("网页读取只允许不带凭据的公开 HTTP(S) URL。")
            urls.append(safe)
    return urls


def map_public_research_text(
    *,
    payload: Any,
    provider: str,
    profile: str,
    arguments: dict[str, Any] | None = None,
) -> list[ExternalSource]:
    """将已知文本格式转为可引用证据，未知格式返回空结果交质量门处理。"""

    if not isinstance(payload, str) or not payload.strip():
        return []
    # 限制扫描量；Client 自身还有限制网络响应字节数。
    text = payload[:100_000]
    if profile == "library_catalog":
        return _library_sources(text=text, provider=provider)
    if profile not in {"web_pages", "fetched_pages", "documentation"}:
        return []
    markers = list(_SOURCE_MARKER.finditer(text))
    sources: list[ExternalSource] = []
    seen: set[tuple[str, str]] = set()
    total = 0
    expected_urls = {
        urldefrag(url)[0] for url in (arguments or {}).get("urls", []) if isinstance(url, str)
    }
    for index, marker in enumerate(markers):
        url = public_url(marker.group(1))
        if not url or profile == "fetched_pages" and urldefrag(url)[0] not in expected_urls:
            continue
        previous_end = markers[index - 1].end() if index else 0
        headings = list(_HEADING.finditer(text, previous_end, marker.start()))
        title = redact_sensitive_text(headings[-1].group(1).strip() if headings else urlsplit(url).hostname or "公共资料")[:240]
        end = markers[index + 1].start() if index + 1 < len(markers) else len(text)
        # 已知格式把下一条标题紧挨着 URL/Source 放置。它属于下一条来源，
        # 不能夹进当前正文；其他未知布局不猜测边界，避免误删有效段落。
        if index + 1 < len(markers):
            next_heading = _NEXT_SOURCE_HEADING.search(text, marker.end(), end)
            if next_heading:
                end = next_heading.start()
        body = text[marker.end():end].strip()
        if len(body) < 40 or _ERROR_BODY.match(body):
            continue
        display = redact_sensitive_text(body)[:min(MAX_SOURCE_CHARS, MAX_TOTAL_CHARS - total)]
        identity = (url, display)
        if not display or identity in seen:
            continue
        seen.add(identity)
        raw = {"url": url, "title": title[:240], "content": display}
        if profile == "documentation" and isinstance((arguments or {}).get("libraryId"), str):
            raw["library_id"] = arguments["libraryId"]
        sources.append(ExternalSource(
            source_type="web" if profile != "documentation" else "documentation",
            provider=provider,
            title=redact_sensitive_text(title)[:240],
            display_text=display,
            url=url,
            rank=len(sources) + 1,
            citation_label=f"[{'D' if profile == 'documentation' else 'W'}{len(sources) + 1}]",
            metadata={"source": "mcp", "raw": raw, "truncated": len(body) > len(display)},
        ))
        total += len(display)
        if len(sources) >= MAX_SOURCES or total >= MAX_TOTAL_CHARS:
            break
    return sources


def _library_sources(*, text: str, provider: str) -> list[ExternalSource]:
    """只从明确的库 ID 标记生成候选，不把错误消息包装成库列表。"""

    sources: list[ExternalSource] = []
    seen: set[str] = set()
    for block in re.split(r"\n-{5,}\s*\n", text):
        match = _LIBRARY_ID.search(block)
        if not match:
            continue
        library_id = match.group(1)
        if not re.fullmatch(r"/[A-Za-z0-9_][A-Za-z0-9_.-]*/[A-Za-z0-9_][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_][A-Za-z0-9_.-]*)?", library_id) or library_id in seen:
            continue
        title_match = re.search(r"^-[ \t]+Title:[ \t]*(.+)$", block, re.MULTILINE)
        description_match = re.search(r"^-[ \t]+Description:[ \t]*(.+)$", block, re.MULTILINE)
        if not title_match or not description_match:
            continue
        title = redact_sensitive_text(title_match.group(1))[:240]
        description = redact_sensitive_text(description_match.group(1))[:1_200]
        url = "https://context7.com" + library_id
        sources.append(ExternalSource(
            source_type="documentation_library", provider=provider, title=title,
            display_text=f"库：{title}\n库 ID：{library_id}\n说明：{description}", url=url,
            rank=len(sources) + 1, citation_label=f"[L{len(sources) + 1}]",
            metadata={"source": "mcp", "raw": {"library_id": library_id, "title": title, "url": url, "content": description}},
        ))
        seen.add(library_id)
        if len(sources) >= MAX_SOURCES:
            break
    return sources
