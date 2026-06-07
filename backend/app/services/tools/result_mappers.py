from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlparse

from app.services.tools.schemas import ExternalSource


def map_mcp_result(
    *,
    mapper: str,
    provider: str,
    category: str,
    display_name: str,
    query: str,
    raw: dict[str, Any],
) -> list[ExternalSource]:
    payload = _extract_payload(raw)
    if mapper == "tavily_search":
        return _map_tavily(payload)
    if mapper == "amap_weather":
        return _map_amap_weather(payload=payload, provider=provider, title=display_name)
    if mapper == "amap_map":
        return _map_amap_map(payload=payload, provider=provider, title=display_name)
    if mapper == "amap_geo":
        return _map_amap_geo(payload=payload, provider=provider, title=display_name)
    if mapper == "amap_distance":
        return _map_amap_distance(payload=payload, provider=provider, title=display_name)
    return _map_generic_mcp_payload(
        payload=payload,
        provider=provider,
        source_type=category,
        title=display_name,
        citation_prefix="T",
    )


def _extract_payload(raw: dict[str, Any]) -> Any:
    result = raw.get("result") or raw
    content = result.get("content") if isinstance(result, dict) else None
    if isinstance(content, list) and content:
        texts: list[str] = []
        structured_items: list[Any] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text" and item.get("text") is not None:
                text = str(item["text"])
                parsed = _try_parse_json(text)
                structured_items.append(parsed if parsed is not None else text)
                texts.append(text)
            elif item.get("text") is not None:
                texts.append(str(item["text"]))
        if len(structured_items) == 1:
            return structured_items[0]
        if structured_items:
            return structured_items
        return "\n".join(texts)
    structured = result.get("structuredContent") if isinstance(result, dict) else None
    if structured:
        return structured
    return result


def _try_parse_json(value: str) -> Any:
    text = value.strip()
    if not text or text[0] not in "[{":
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _map_tavily(payload: Any) -> list[ExternalSource]:
    data = payload if isinstance(payload, dict) else {}
    sources: list[ExternalSource] = []
    answer = str(data.get("answer") or "").strip()
    if answer:
        sources.append(
            ExternalSource(
                source_type="web",
                provider="tavily",
                title="Tavily 综合摘要",
                display_text=answer,
                rank=0,
                citation_label="[S]",
                metadata={"source": "mcp"},
            )
        )

    for index, item in enumerate(data.get("results") or [], start=1):
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip() or None
        title = str(item.get("title") or "").strip() or (urlparse(url or "").netloc or "搜索结果")
        content = str(item.get("content") or item.get("snippet") or "").strip()
        if not content and not url:
            continue
        sources.append(
            ExternalSource(
                source_type="web",
                provider="tavily",
                title=title,
                url=url,
                display_text=content[:1600],
                rank=index,
                score=item.get("score"),
                citation_label=f"[{index}]",
                metadata={"domain": urlparse(url or "").netloc, "source": "mcp"},
            )
        )
    if sources:
        return sources
    return _map_generic_mcp_payload(
        payload=payload,
        provider="tavily",
        source_type="web",
        title="Tavily 搜索",
        citation_prefix="S",
    )


def _map_amap_weather(*, payload: Any, provider: str, title: str) -> list[ExternalSource]:
    data = payload if isinstance(payload, dict) else {}
    lives = data.get("lives") if isinstance(data.get("lives"), list) else []
    forecasts = data.get("forecasts") if isinstance(data.get("forecasts"), list) else []
    if not lives and not forecasts:
        return []
    if lives:
        return _map_generic_mcp_payload(
            payload=lives,
            provider=provider,
            source_type="weather",
            title=title,
            citation_prefix="W",
        )
    return _map_generic_mcp_payload(
        payload=forecasts,
        provider=provider,
        source_type="weather",
        title=title,
        citation_prefix="W",
    )


def _map_amap_map(*, payload: Any, provider: str, title: str) -> list[ExternalSource]:
    data = payload if isinstance(payload, dict) else {}
    if isinstance(data.get("route"), dict):
        data = data["route"]
    paths = data.get("paths") if isinstance(data.get("paths"), list) else []
    if paths:
        path = paths[0] if isinstance(paths[0], dict) else {}
        display = _format_amap_route_display(data=data, path=path)
        return [
            ExternalSource(
                source_type="map",
                provider=provider,
                title=title,
                display_text=display,
                rank=1,
                citation_label="[M1]",
                metadata={"source": "mcp", "raw": data},
            )
        ]
    return _map_generic_mcp_payload(
        payload=payload,
        provider=provider,
        source_type="map",
        title=title,
        citation_prefix="M",
    )


def _map_amap_geo(*, payload: Any, provider: str, title: str) -> list[ExternalSource]:
    data = payload if isinstance(payload, dict) else {}
    geocodes = data.get("geocodes") if isinstance(data.get("geocodes"), list) else []
    if geocodes:
        return _map_generic_mcp_payload(
            payload=geocodes[:3],
            provider=provider,
            source_type="map",
            title=title,
            citation_prefix="G",
        )
    return _map_generic_mcp_payload(
        payload=payload,
        provider=provider,
        source_type="map",
        title=title,
        citation_prefix="G",
    )


def _map_amap_distance(*, payload: Any, provider: str, title: str) -> list[ExternalSource]:
    data = payload if isinstance(payload, dict) else {}
    results = data.get("results") if isinstance(data.get("results"), list) else []
    if not results and isinstance(data.get("distance"), str):
        results = [data]
    if results:
        lines: list[str] = []
        for index, item in enumerate(results, start=1):
            if not isinstance(item, dict):
                continue
            distance = _format_meters(item.get("distance"))
            duration = _format_seconds(item.get("duration"))
            origin_id = item.get("origin_id") or item.get("origin") or index
            lines.append(f"起点 {origin_id}：距离 {distance}，预计耗时 {duration}")
        if lines:
            return [
                ExternalSource(
                    source_type="map",
                    provider=provider,
                    title=title,
                    display_text="\n".join(lines),
                    rank=1,
                    citation_label="[D1]",
                    metadata={"source": "mcp", "raw": data},
                )
            ]
    return _map_generic_mcp_payload(
        payload=payload,
        provider=provider,
        source_type="map",
        title=title,
        citation_prefix="D",
    )


def _format_amap_route_display(*, data: dict[str, Any], path: dict[str, Any]) -> str:
    origin = str(data.get("origin") or "").strip()
    destination = str(data.get("destination") or "").strip()
    distance = _format_meters(path.get("distance"))
    duration = _format_seconds(path.get("duration"))
    steps = path.get("steps") if isinstance(path.get("steps"), list) else []
    instructions: list[str] = []
    for step in steps[:8]:
        if isinstance(step, dict) and step.get("instruction"):
            instructions.append(str(step["instruction"]))
    lines = [
        f"起点坐标：{origin or '未知'}",
        f"终点坐标：{destination or '未知'}",
        f"距离：{distance}",
        f"预计耗时：{duration}",
    ]
    if instructions:
        lines.append(f"路线步骤：{'；'.join(instructions)}")
    return "\n".join(lines)


def _format_meters(value: Any) -> str:
    try:
        meters = float(str(value or "").strip())
    except ValueError:
        return str(value or "未知")
    if meters >= 1000:
        return f"{meters / 1000:.1f} 公里"
    return f"{int(meters)} 米"


def _format_seconds(value: Any) -> str:
    try:
        seconds = int(float(str(value or "").strip()))
    except ValueError:
        return str(value or "未知")
    if seconds >= 3600:
        return f"{seconds // 3600} 小时 {(seconds % 3600) // 60} 分钟"
    if seconds >= 60:
        return f"{seconds // 60} 分钟"
    return f"{seconds} 秒"


def _map_generic_mcp_payload(
    *,
    payload: Any,
    provider: str,
    source_type: str,
    title: str,
    citation_prefix: str,
) -> list[ExternalSource]:
    if payload in (None, "", [], {}):
        return []

    if isinstance(payload, list):
        items = payload[:6]
    else:
        items = [payload]

    sources: list[ExternalSource] = []
    for index, item in enumerate(items, start=1):
        display = item if isinstance(item, str) else json.dumps(item, ensure_ascii=False, indent=2)
        if not display.strip():
            continue
        sources.append(
            ExternalSource(
                source_type=source_type,
                provider=provider,
                title=f"{title}结果 {index}",
                display_text=display[:2400],
                rank=index,
                citation_label=f"[{citation_prefix}{index}]",
                metadata={"source": "mcp", "raw": item if isinstance(item, dict) else None},
            )
        )
    return sources
