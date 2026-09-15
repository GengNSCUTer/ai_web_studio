from __future__ import annotations

import json
import math
from typing import Any
from urllib.parse import urlparse

from app.services.tools.onboarding import ToolOnboardingContractError, validate_canonical_mapper
from app.services.tools.quality import resolve_json_pointer
from app.services.tools.schemas import ExternalSource, redact_sensitive_text


MAX_CANONICAL_EVIDENCE_CHARS = 1600


def map_mcp_result(
    *,
    mapper: str,
    provider: str,
    category: str,
    display_name: str,
    query: str,
    raw: dict[str, Any],
    canonical_mapper: dict[str, Any] | None = None,
) -> list[ExternalSource]:
    payload = extract_mcp_payload(raw)
    if mapper == "tavily_search":
        return _map_tavily(payload)
    if mapper == "amap_weather":
        return _map_amap_weather(payload=payload, provider=provider, title=display_name)
    if mapper == "amap_geo":
        return _map_amap_geo(payload=payload, provider=provider, title=display_name)
    if mapper == "amap_distance":
        return _map_amap_distance(payload=payload, provider=provider, title=display_name)
    if mapper == "amap_route":
        return _map_amap_route(payload=payload, provider=provider, title=display_name)
    if mapper == "amap_poi":
        return _map_amap_poi(payload=payload, provider=provider, title=display_name)
    if mapper == "amap_map":
        # 兼容历史动态 MCP 配置；内置 Tool 已改用 geo/route/poi 专用 Mapper。
        return _map_amap_legacy_map(payload=payload, provider=provider, title=display_name)
    if mapper == "declared_canonical":
        return _map_declared_canonical_payload(
            payload=payload,
            mapper=canonical_mapper,
            provider=provider,
            source_type=category,
            title=display_name,
        )
    return _map_generic_mcp_payload(
        payload=payload,
        provider=provider,
        source_type=category,
        title=display_name,
        citation_prefix="T",
    )


def map_declared_mcp_result(
    *,
    canonical_mapper: dict[str, Any],
    provider: str,
    category: str,
    display_name: str,
    raw: dict[str, Any],
) -> list[ExternalSource]:
    """供离线 fixture 验证复用的声明式 MCP 映射入口。

    该函数只读取 MCP 的结构化 payload，不读取网络、不执行模板或表达式；输出
    也不会保留整个 Provider 响应。生产 Adapter 通过 ``map_mcp_result`` 调用
    同一实现，避免“fixture 能通过、线上走另一套 Mapper”的分叉。
    """

    return _map_declared_canonical_payload(
        payload=extract_mcp_payload(raw),
        mapper=canonical_mapper,
        provider=provider,
        source_type=category,
        title=display_name,
    )


def _map_declared_canonical_payload(
    *,
    payload: Any,
    mapper: dict[str, Any] | None,
    provider: str,
    source_type: str,
    title: str,
) -> list[ExternalSource]:
    """把受限 object/collection 映射投影为最小可校验 Source。

    任何合同损坏、根路径不匹配、item 非对象或缺少 display 字段的情况都返回
    空列表。后续质量合同会将其收口为 invalid；不能退回 generic JSON evidence。
    """

    try:
        normalized = validate_canonical_mapper(mapper)
    except ToolOnboardingContractError:
        return []

    mapper_type = normalized["type"]
    root_field = "object_path" if mapper_type == "object" else "collection_path"
    exists, root = resolve_json_pointer(payload, normalized[root_field])
    if not exists:
        return []
    if mapper_type == "object":
        items = [root] if isinstance(root, dict) else []
    else:
        items = root[: normalized["max_items"]] if isinstance(root, list) else []

    sources: list[ExternalSource] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        display = _declared_text(item, normalized["display_text_path"], normalized["max_chars"])
        if not display:
            continue
        source_title = _declared_text(item, normalized.get("title_path"), 240)
        safe_url = _declared_url(item, normalized.get("url_path"))
        score = _declared_score(item, normalized.get("score_path"))
        canonical = _declared_canonical_fields(
            item=item,
            field_paths=normalized["canonical_fields"],
            max_chars=normalized["max_chars"],
        )
        # 约束同名 URL 也必须是 http(s)，防止质量合同仅凭 ``javascript:``
        # 等恶意字符串通过 identity 检查。其它业务 identity 仍由 profile 定义。
        if "url" in canonical:
            canonical["url"] = _safe_http_url(canonical["url"]) or ""
        source_index = len(sources) + 1
        sources.append(
            ExternalSource(
                source_type=source_type,
                provider=redact_sensitive_text(provider)[:96],
                title=source_title or redact_sensitive_text(title)[:240] or f"MCP 结果 {source_index}",
                url=safe_url,
                display_text=display,
                rank=source_index,
                score=score,
                citation_label=f"[MCP{source_index}]",
                metadata=_canonical_metadata(canonical),
            )
        )
    return sources


def _declared_canonical_fields(
    *,
    item: dict[str, Any],
    field_paths: dict[str, str],
    max_chars: int,
) -> dict[str, Any]:
    """仅保留声明列出的标量字段，禁止把 dict/list 原样带入 metadata。"""

    result: dict[str, Any] = {}
    for field_name, pointer in field_paths.items():
        exists, value = resolve_json_pointer(item, pointer)
        if not exists:
            continue
        scalar = _declared_scalar(value, max_chars=max_chars)
        if scalar is not None:
            result[field_name] = scalar
    return result


def _declared_text(item: dict[str, Any], pointer: Any, max_chars: int) -> str:
    if not isinstance(pointer, str):
        return ""
    exists, value = resolve_json_pointer(item, pointer)
    if not exists:
        return ""
    scalar = _declared_scalar(value, max_chars=max_chars)
    return scalar.strip() if isinstance(scalar, str) else ""


def _declared_url(item: dict[str, Any], pointer: Any) -> str | None:
    if not isinstance(pointer, str):
        return None
    exists, value = resolve_json_pointer(item, pointer)
    if not exists:
        return None
    scalar = _declared_scalar(value, max_chars=2048)
    return _safe_http_url(scalar)


def _declared_score(item: dict[str, Any], pointer: Any) -> float | None:
    if not isinstance(pointer, str):
        return None
    exists, value = resolve_json_pointer(item, pointer)
    if not exists or isinstance(value, bool):
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    return score if math.isfinite(score) else None


def _declared_scalar(value: Any, *, max_chars: int) -> str | int | float | None:
    if value is None or isinstance(value, bool) or isinstance(value, (dict, list, tuple, set)):
        return None
    if isinstance(value, str):
        return redact_sensitive_text(value).strip()[:max_chars]
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return None


def _safe_http_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = redact_sensitive_text(value).strip()
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None
    return candidate


def extract_mcp_payload(raw: dict[str, Any]) -> Any:
    """提取 MCP 的机器可读结果，优先 structuredContent 再兼容文本 JSON。"""

    result = raw["result"] if "result" in raw else raw
    # MCP outputSchema 对应的 structuredContent 是机器可读主结果；content 中的 JSON 文本只是兼容副本。
    if isinstance(result, dict) and "structuredContent" in result:
        return result["structuredContent"]
    content_present = isinstance(result, dict) and "content" in result
    content = result.get("content") if content_present else None
    if isinstance(content, list):
        texts: list[str] = []
        parsed_payloads: list[Any] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            if item.get("type") == "text" and item.get("text") is not None:
                text = str(item["text"])
                parsed = _try_parse_json(text)
                if parsed is not None:
                    parsed_payloads.append(parsed)
                texts.append(text)
            elif item.get("text") is not None:
                texts.append(str(item["text"]))
        if parsed_payloads:
            # 部分 MCP 会先给人类可读说明，再给一段 JSON 机器结果。不能把说明文本
            # 和 JSON 混成 list，否则专用 Mapper 会把它误认为非结构化响应。若服务端
            # 异常地返回多段 JSON，不做未经定义的合并，保守采用第一段结构化载荷。
            return parsed_payloads[0]
        return "\n".join(texts) if texts else None
    if content_present:
        return None
    return result


def _extract_payload(raw: dict[str, Any]) -> Any:
    """保留旧私有名称，避免历史测试或调用方在迁移期间中断。"""

    return extract_mcp_payload(raw)


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

    for index, item in enumerate(data.get("results") or [], start=1):
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip() or None
        title = str(item.get("title") or "").strip() or (urlparse(url or "").netloc or "搜索结果")
        content = str(item.get("content") or item.get("snippet") or "").strip()
        # 只有 URL 没有正文不能作为可供回答使用的外部证据。
        if not content or not url:
            continue
        # Canonical raw 只服务于质量门与受控绑定：必须有界，也必须在 Mapper
        # 层先脱敏。Executor/序列化层会再次脱敏，形成纵深防护。
        safe_url = redact_sensitive_text(url)
        safe_title = redact_sensitive_text(title)
        safe_content = redact_sensitive_text(content[:MAX_CANONICAL_EVIDENCE_CHARS])
        sources.append(
            ExternalSource(
                source_type="web",
                provider="tavily",
                title=safe_title,
                url=safe_url,
                display_text=safe_content,
                rank=index,
                score=item.get("score"),
                citation_label=f"[{index}]",
                metadata={
                    **_canonical_metadata(
                        {
                            "url": safe_url,
                            "title": safe_title,
                            "content": safe_content,
                            "score": item.get("score"),
                        }
                    ),
                    "domain": urlparse(safe_url).netloc,
                },
            )
        )
    # 内置网页搜索只接受具有正文的可引用结果；不能把未知 JSON 退化成证据。
    return sources


def _map_amap_weather(*, payload: Any, provider: str, title: str) -> list[ExternalSource]:
    data = payload if isinstance(payload, dict) else {}
    lives = data.get("lives") if isinstance(data.get("lives"), list) else []
    forecasts = data.get("forecasts") if isinstance(data.get("forecasts"), list) else []
    if lives:
        return _map_amap_live_weather(lives=lives, provider=provider, title=title)
    if forecasts:
        return _map_amap_forecast_weather(
            city=_text(data.get("city")),
            forecasts=forecasts,
            provider=provider,
            title=title,
        )
    return []


def _map_amap_live_weather(
    *,
    lives: list[Any],
    provider: str,
    title: str,
) -> list[ExternalSource]:
    """将高德实况天气归一化为每条可独立校验的 Source。"""

    sources: list[ExternalSource] = []
    for index, item in enumerate(lives[:6], start=1):
        if not isinstance(item, dict):
            continue
        city = _text(item.get("city"))
        weather = _text(item.get("weather"))
        temperature = _text(item.get("temperature"))
        report_time = _text(item.get("reporttime"))
        if not city or not weather:
            continue
        display = f"{city}当前天气：{weather}"
        if temperature:
            display += f"，气温 {temperature} 摄氏度"
        if report_time:
            display += f"，发布时间 {report_time}"
        sources.append(
            ExternalSource(
                source_type="weather",
                provider=provider,
                title=f"{title}：{city}",
                display_text=display,
                rank=index,
                citation_label=f"[W{index}]",
                metadata=_canonical_metadata(
                    {
                        "city": city,
                        "adcode": _text(item.get("adcode")),
                        "weather": weather,
                        "temperature": temperature,
                        "reporttime": report_time,
                    }
                ),
            )
        )
    return sources


def _map_amap_forecast_weather(
    *,
    city: str,
    forecasts: list[Any],
    provider: str,
    title: str,
) -> list[ExternalSource]:
    """将高德预报天气归一化；没有城市或天气字段时宁可返回空结果。"""

    if not city:
        return []
    sources: list[ExternalSource] = []
    for index, item in enumerate(forecasts[:7], start=1):
        if not isinstance(item, dict):
            continue
        date = _text(item.get("date"))
        day_weather = _text(item.get("dayweather"))
        night_weather = _text(item.get("nightweather"))
        weather = " / ".join(part for part in (day_weather, night_weather) if part)
        if not weather:
            continue
        day_temp = _text(item.get("daytemp"))
        night_temp = _text(item.get("nighttemp"))
        display = f"{city}{date or '近期'}天气：{weather}"
        if day_temp or night_temp:
            display += f"，气温 {day_temp or '未知'}～{night_temp or '未知'} 摄氏度"
        sources.append(
            ExternalSource(
                source_type="weather",
                provider=provider,
                title=f"{title}：{city}{date}",
                display_text=display,
                rank=index,
                citation_label=f"[W{index}]",
                metadata=_canonical_metadata(
                    {
                        "city": city,
                        "adcode": _text(item.get("adcode")),
                        "weather": weather,
                        "temperature": "～".join(part for part in (day_temp, night_temp) if part),
                        "reporttime": date,
                    }
                ),
            )
        )
    return sources


def _map_amap_legacy_map(*, payload: Any, provider: str, title: str) -> list[ExternalSource]:
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
    # 高德 MCP 当前返回 results；保留 geocodes 兼容旧协议形态。
    records = data.get("results") if isinstance(data.get("results"), list) else data.get("geocodes")
    if not isinstance(records, list):
        return []

    sources: list[ExternalSource] = []
    for index, item in enumerate(records[:6], start=1):
        if not isinstance(item, dict):
            continue
        location = _text(item.get("location") or item.get("lnglat") or item.get("coordinates"))
        address = _first_text(
            item.get("formatted_address"),
            item.get("address"),
            item.get("name"),
            _join_location_parts(item, keys=("province", "city", "district", "street", "number")),
        )
        if not location or not address:
            continue
        display = f"匹配地址：{address}\n坐标：{location}"
        sources.append(
            ExternalSource(
                source_type="map",
                provider=provider,
                title=f"{title}：{address}",
                display_text=display,
                rank=index,
                citation_label=f"[G{index}]",
                metadata=_canonical_metadata(
                    {
                        "formatted_address": address,
                        "location": location,
                        "adcode": _text(item.get("adcode")),
                        "city": _text(item.get("city")),
                        "level": _text(item.get("level")),
                    }
                ),
            )
        )
    return sources


def _map_amap_distance(*, payload: Any, provider: str, title: str) -> list[ExternalSource]:
    data = payload if isinstance(payload, dict) else {}
    results = data.get("results") if isinstance(data.get("results"), list) else []
    if not results and isinstance(data.get("distance"), str):
        results = [data]
    sources: list[ExternalSource] = []
    for index, item in enumerate(results[:6], start=1):
        if not isinstance(item, dict):
            continue
        raw_distance = _text(item.get("distance"))
        if not raw_distance:
            continue
        origin_id = _text(item.get("origin_id") or item.get("origin"))
        destination_id = _text(item.get("dest_id") or item.get("destination"))
        identity = " → ".join(part for part in (origin_id, destination_id) if part)
        if not identity:
            continue
        duration = _text(item.get("duration"))
        display = f"{identity}：距离 {_format_meters(raw_distance)}"
        if duration:
            display += f"，预计耗时 {_format_seconds(duration)}"
        sources.append(
            ExternalSource(
                source_type="map",
                provider=provider,
                title=title,
                display_text=display,
                rank=index,
                citation_label=f"[D{index}]",
                metadata=_canonical_metadata(
                    {
                        "origin_id": origin_id,
                        "destination_id": destination_id,
                        "distance": raw_distance,
                        "duration": duration,
                    }
                ),
            )
        )
    return sources


def _map_amap_route(*, payload: Any, provider: str, title: str) -> list[ExternalSource]:
    data = payload if isinstance(payload, dict) else {}
    if isinstance(data.get("route"), dict):
        data = data["route"]
    paths = data.get("paths") if isinstance(data.get("paths"), list) else []
    if not paths and isinstance(data.get("transits"), list):
        # 公交/地铁接口返回 transits，不是驾车/步行接口的 paths；统一成一条首选方案。
        first_transit = data["transits"][0] if data["transits"] else {}
        if isinstance(first_transit, dict):
            paths = [
                {
                    "distance": data.get("distance"),
                    "duration": first_transit.get("duration"),
                    "steps": [],
                }
            ]
    origin = _text(data.get("origin"))
    destination = _text(data.get("destination"))
    identity = " → ".join(part for part in (origin, destination) if part)
    if not origin or not destination:
        return []

    sources: list[ExternalSource] = []
    for index, path in enumerate(paths[:3], start=1):
        if not isinstance(path, dict):
            continue
        distance = _text(path.get("distance"))
        duration = _text(path.get("duration"))
        if not distance and not duration:
            continue
        display = _format_amap_route_display(data=data, path=path)
        sources.append(
            ExternalSource(
                source_type="map",
                provider=provider,
                title=title,
                display_text=display,
                rank=index,
                citation_label=f"[M{index}]",
                metadata=_canonical_metadata(
                    {
                        "origin": origin,
                        "destination": destination,
                        "distance": distance,
                        "duration": duration,
                    }
                ),
            )
        )
    return sources


def _map_amap_poi(*, payload: Any, provider: str, title: str) -> list[ExternalSource]:
    data = payload if isinstance(payload, dict) else {}
    pois = data.get("pois") if isinstance(data.get("pois"), list) else []
    sources: list[ExternalSource] = []
    for index, item in enumerate(pois[:8], start=1):
        if not isinstance(item, dict):
            continue
        poi_id = _text(item.get("id"))
        name = _text(item.get("name"))
        address = _text(item.get("address"))
        location = _text(item.get("location"))
        if not poi_id or not name:
            continue
        display_parts = [f"名称：{name}"]
        if address:
            display_parts.append(f"地址：{address}")
        if location:
            display_parts.append(f"坐标：{location}")
        type_code = _text(item.get("type") or item.get("typecode"))
        if type_code:
            display_parts.append(f"类型：{type_code}")
        display = "\n".join(display_parts)
        sources.append(
            ExternalSource(
                source_type="map",
                provider=provider,
                title=f"{title}：{name}",
                display_text=display,
                rank=index,
                citation_label=f"[P{index}]",
                metadata=_canonical_metadata(
                    {
                        "id": poi_id,
                        "name": name,
                        "location": location,
                        "address": address,
                        "type": type_code,
                    }
                ),
            )
        )
    return sources


def _canonical_metadata(raw: dict[str, Any]) -> dict[str, Any]:
    """只保留质量验证与结果绑定需要的 Provider 字段，避免保存完整远端响应。"""

    return {"source": "mcp", "raw": raw}


def _text(value: Any) -> str:
    """将 Provider 标量转为去空白文本，不把 list/dict 伪装成业务字段。"""

    if value is None or isinstance(value, (dict, list, tuple, set)):
        return ""
    return str(value).strip()


def _first_text(*values: Any) -> str:
    for value in values:
        text = _text(value)
        if text:
            return text
    return ""


def _join_location_parts(item: dict[str, Any], *, keys: tuple[str, ...]) -> str:
    return "".join(_text(item.get(key)) for key in keys)


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
