from __future__ import annotations

import json
import re
from typing import Any

from app.services.tools.mcp_client import McpHttpClient
from app.services.tools.mcp_security import enforce_mcp_endpoint_target_policy
from app.services.tools.providers.amap import AmapToolProvider
from app.services.tools.providers.tavily import TavilySearchProvider
from app.services.tools.result_mappers import map_mcp_result
from app.services.tools.schemas import ExternalSource, PlannedToolCall, ToolDefinition


class ToolAdapterRunner:
    def __init__(
        self,
        *,
        tavily_provider: TavilySearchProvider | None = None,
        amap_provider: AmapToolProvider | None = None,
    ) -> None:
        self.tavily_provider = tavily_provider or TavilySearchProvider()
        self.amap_provider = amap_provider or AmapToolProvider()

    async def run(
        self,
        *,
        definition: ToolDefinition,
        call: PlannedToolCall,
        api_key: str | None,
    ) -> tuple[list[ExternalSource], dict[str, Any]]:
        if definition.adapter_type == "mcp_http":
            try:
                sources, metadata = await self._run_mcp_http(definition=definition, call=call, api_key=api_key)
                if sources:
                    return sources, metadata
                return await self._fallback_or_empty(definition=definition, call=call, api_key=api_key, reason="mcp_empty_result")
            except Exception as exc:
                return await self._fallback_or_raise(definition=definition, call=call, api_key=api_key, reason=str(exc))
        if definition.adapter_type == "python":
            return await self._run_python(definition=definition, call=call, api_key=api_key)
        raise RuntimeError(f"未知工具 adapter 类型：{definition.adapter_type}")

    async def _fallback_or_empty(
        self,
        *,
        definition: ToolDefinition,
        call: PlannedToolCall,
        api_key: str | None,
        reason: str,
    ) -> tuple[list[ExternalSource], dict[str, Any]]:
        fallback_method = str(definition.adapter.get("fallback_python_method") or "")
        if not fallback_method:
            return [], {"adapter_type": definition.adapter_type, "fallback_reason": reason}
        sources, metadata = await self._run_python_method(method=fallback_method, call=call, api_key=api_key)
        metadata.update({"fallback_from": "mcp_http", "fallback_reason": reason})
        return sources, metadata

    async def _fallback_or_raise(
        self,
        *,
        definition: ToolDefinition,
        call: PlannedToolCall,
        api_key: str | None,
        reason: str,
    ) -> tuple[list[ExternalSource], dict[str, Any]]:
        fallback_method = str(definition.adapter.get("fallback_python_method") or "")
        if not fallback_method:
            raise RuntimeError(reason)
        sources, metadata = await self._run_python_method(method=fallback_method, call=call, api_key=api_key)
        metadata.update({"fallback_from": "mcp_http", "fallback_reason": reason})
        return sources, metadata

    async def _run_mcp_http(
        self,
        *,
        definition: ToolDefinition,
        call: PlannedToolCall,
        api_key: str | None,
    ) -> tuple[list[ExternalSource], dict[str, Any]]:
        endpoint_template = str(definition.adapter.get("endpoint_template") or "")
        mcp_tool_name = str(definition.adapter.get("mcp_tool_name") or "")
        mcp_workflow = str(definition.adapter.get("mcp_workflow") or "")
        auth_type = str(definition.adapter.get("auth_type") or "api_key").strip() or "api_key"
        if not endpoint_template or not (mcp_tool_name or mcp_workflow):
            raise RuntimeError(f"工具 {definition.tool_key} 缺少 MCP endpoint 或 tool/workflow name。")
        needs_api_key = auth_type != "none" or "{api_key}" in endpoint_template
        if needs_api_key and not api_key:
            raise RuntimeError(f"工具 {definition.tool_key} 未配置 API Key。")

        endpoint = endpoint_template.replace("{api_key}", api_key or "")
        if definition.source_type == "mcp_server":
            # 内置 manifest endpoint 由项目维护；用户动态添加的 Server 必须在每次调用前做 SSRF 检查。
            await enforce_mcp_endpoint_target_policy(endpoint)
        arguments = self._build_adapter_arguments(definition=definition, call=call)
        client = McpHttpClient(endpoint=endpoint, extra_headers=self._mcp_auth_headers(auth_type=auth_type, api_key=api_key))
        arguments = await self._normalize_raw_amap_arguments(client=client, mcp_tool_name=mcp_tool_name, arguments=arguments)

        if mcp_workflow == "amap_weather_query":
            return await self._run_amap_weather_workflow(client=client, definition=definition, call=call, arguments=arguments)
        if mcp_workflow == "amap_route_plan":
            return await self._run_amap_route_workflow(client=client, definition=definition, call=call, arguments=arguments)
        if mcp_workflow == "amap_distance_measure":
            return await self._run_amap_distance_workflow(client=client, definition=definition, call=call, arguments=arguments)
        if mcp_workflow == "amap_poi_search":
            return await self._run_amap_poi_workflow(client=client, definition=definition, call=call, arguments=arguments)

        response = await client.call_tool(
            tool_name=mcp_tool_name,
            arguments=arguments,
            output_schema=definition.output_schema or None,
        )
        sources = map_mcp_result(
            mapper=str(definition.adapter.get("result_mapper") or ""),
            provider=definition.provider,
            category=definition.category,
            display_name=definition.display_name,
            query=str(call.arguments.get("query") or ""),
            raw=response.raw,
        )
        return sources, {"adapter_type": "mcp_http", "mcp_tool_name": mcp_tool_name, "mcp_arguments": arguments}

    async def _normalize_raw_amap_arguments(
        self,
        *,
        client: McpHttpClient,
        mcp_tool_name: str,
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        if mcp_tool_name in {"maps_direction_driving", "maps_direction_walking"}:
            normalized = dict(arguments)
            normalized["origin"] = await self._ensure_amap_location(client=client, value=str(normalized.get("origin") or ""))
            normalized["destination"] = await self._ensure_amap_location(
                client=client,
                value=str(normalized.get("destination") or ""),
            )
            return normalized
        if mcp_tool_name == "maps_direction_transit_integrated":
            normalized = dict(arguments)
            origin_text = str(normalized.get("origin") or "")
            destination_text = str(normalized.get("destination") or "")
            origin_geo = await self._mcp_amap_geo(client=client, address=origin_text) if not self._is_amap_location(origin_text) else {}
            destination_geo = (
                await self._mcp_amap_geo(client=client, address=destination_text)
                if not self._is_amap_location(destination_text)
                else {}
            )
            normalized["origin"] = self._extract_amap_location(origin_geo) if origin_geo else origin_text
            normalized["destination"] = self._extract_amap_location(destination_geo) if destination_geo else destination_text
            normalized["city"] = normalized.get("city") or self._extract_amap_city(origin_geo)
            normalized["cityd"] = normalized.get("cityd") or self._extract_amap_city(destination_geo)
            return normalized
        if mcp_tool_name == "maps_distance":
            normalized = dict(arguments)
            origins = normalized.get("origins") or ""
            if isinstance(origins, list):
                origin_items = [str(item).strip() for item in origins if str(item).strip()]
            else:
                origin_items = [item.strip() for item in str(origins).split("|") if item.strip()]
            normalized_origins = [
                await self._ensure_amap_location(client=client, value=origin)
                for origin in origin_items
            ]
            normalized["origins"] = "|".join(item for item in normalized_origins if item)
            normalized["destination"] = await self._ensure_amap_location(
                client=client,
                value=str(normalized.get("destination") or ""),
            )
            return normalized
        return arguments

    async def _ensure_amap_location(self, *, client: McpHttpClient, value: str) -> str:
        value = value.strip()
        if not value or self._is_amap_location(value):
            return value
        geo = await self._mcp_amap_geo(client=client, address=value)
        location = self._extract_amap_location(geo)
        if not location:
            raise RuntimeError(f"高德 MCP 地理编码未返回有效坐标：{value}")
        return location

    @staticmethod
    def _is_amap_location(value: str) -> bool:
        return bool(re.fullmatch(r"\s*-?\d{1,3}(?:\.\d+)?\s*,\s*-?\d{1,2}(?:\.\d+)?\s*", value or ""))

    @staticmethod
    def _mcp_auth_headers(*, auth_type: str, api_key: str | None) -> dict[str, str]:
        if not api_key:
            return {}
        if auth_type == "bearer":
            return {"Authorization": f"Bearer {api_key}"}
        if auth_type == "api_key_header":
            return {"X-API-Key": api_key}
        return {}

    async def _run_amap_weather_workflow(
        self,
        *,
        client: McpHttpClient,
        definition: ToolDefinition,
        call: PlannedToolCall,
        arguments: dict[str, Any],
    ) -> tuple[list[ExternalSource], dict[str, Any]]:
        city = str(arguments.get("city") or call.arguments.get("city") or "").strip()
        if not city:
            city = AmapToolProvider._extract_city(str(call.arguments.get("query") or ""))
        sources = await self._call_amap_weather(client=client, definition=definition, call=call, city=city)
        if sources:
            return sources, self._mcp_metadata("amap_weather_query", "maps_weather", {"city": city})

        geo = await self._mcp_amap_geo(client=client, address=city)
        candidate = self._extract_amap_adcode_or_city(geo)
        if candidate and candidate != city:
            sources = await self._call_amap_weather(client=client, definition=definition, call=call, city=candidate)
            for source in sources:
                source.metadata.update({"requested_location": city, "resolved_city": candidate})
            if sources:
                metadata = self._mcp_metadata("amap_weather_query", "maps_weather", {"city": candidate})
                metadata["resolved_from"] = city
                return sources, metadata
        return [], self._mcp_metadata("amap_weather_query", "maps_weather", {"city": city})

    async def _call_amap_weather(
        self,
        *,
        client: McpHttpClient,
        definition: ToolDefinition,
        call: PlannedToolCall,
        city: str,
    ) -> list[ExternalSource]:
        response = await client.call_tool(tool_name="maps_weather", arguments={"city": city})
        return map_mcp_result(
            mapper="amap_weather",
            provider=definition.provider,
            category=definition.category,
            display_name=definition.display_name,
            query=str(call.arguments.get("query") or city),
            raw=response.raw,
        )

    async def _run_amap_route_workflow(
        self,
        *,
        client: McpHttpClient,
        definition: ToolDefinition,
        call: PlannedToolCall,
        arguments: dict[str, Any],
    ) -> tuple[list[ExternalSource], dict[str, Any]]:
        query = str(arguments.get("query") or call.arguments.get("query") or "")
        origin_text = str(arguments.get("origin") or "").strip()
        destination_text = str(arguments.get("destination") or "").strip()
        mode = str(arguments.get("mode") or "driving").strip() or "driving"
        if not origin_text or not destination_text:
            route = AmapToolProvider._extract_route_query(query)
            if not route:
                raise RuntimeError("路线规划缺少起点或终点。")
            origin_text, destination_text, mode = route

        origin_geo, destination_geo, origin_location, destination_location = await self._resolve_two_locations(
            client=client,
            origin=origin_text,
            destination=destination_text,
        )
        tool_name = {
            "walking": "maps_direction_walking",
            "transit": "maps_direction_transit_integrated",
            "driving": "maps_direction_driving",
        }.get(mode, "maps_direction_driving")
        route_args: dict[str, Any] = {"origin": origin_location, "destination": destination_location}
        if mode == "transit":
            route_args["city"] = self._extract_amap_city(origin_geo) or origin_text
            route_args["cityd"] = self._extract_amap_city(destination_geo) or destination_text

        response = await client.call_tool(tool_name=tool_name, arguments=route_args)
        sources = map_mcp_result(
            mapper="amap_map",
            provider=definition.provider,
            category=definition.category,
            display_name=f"{definition.display_name}路线",
            query=query,
            raw=response.raw,
        )
        for source in sources:
            source.title = f"高德MCP路线：{origin_text}到{destination_text}"
            source.metadata.update(
                {
                    "origin": origin_text,
                    "destination": destination_text,
                    "origin_location": origin_location,
                    "destination_location": destination_location,
                    "mode": mode,
                    "mcp_tool_name": tool_name,
                }
            )
        return sources, self._mcp_metadata("amap_route_plan", tool_name, route_args)

    async def _run_amap_distance_workflow(
        self,
        *,
        client: McpHttpClient,
        definition: ToolDefinition,
        call: PlannedToolCall,
        arguments: dict[str, Any],
    ) -> tuple[list[ExternalSource], dict[str, Any]]:
        query = str(arguments.get("query") or call.arguments.get("query") or "")
        origins = arguments.get("origins") or []
        if isinstance(origins, str):
            origins = [item.strip() for item in origins.split("|") if item.strip()]
        destination_text = str(arguments.get("destination") or "").strip()
        mode = str(arguments.get("mode") or "driving").strip() or "driving"
        if (not origins or not destination_text) and query:
            route = AmapToolProvider._extract_route_query(query)
            if route:
                origin_text, destination_text, mode = route
                origins = [origin_text]
        if not origins or not destination_text:
            raise RuntimeError("距离测量缺少起点或终点。")

        origin_locations: list[str] = []
        for origin in origins[:8]:
            geo = await self._mcp_amap_geo(client=client, address=str(origin))
            location = self._extract_amap_location(geo)
            if location:
                origin_locations.append(location)
        destination_geo = await self._mcp_amap_geo(client=client, address=destination_text)
        destination_location = self._extract_amap_location(destination_geo)
        if not origin_locations or not destination_location:
            raise RuntimeError("高德 MCP 地理编码未返回有效距离测量坐标。")

        distance_args = {
            "origins": "|".join(origin_locations),
            "destination": destination_location,
            "type": {"straight": "0", "driving": "1", "walking": "3"}.get(mode, "1"),
        }
        response = await client.call_tool(tool_name="maps_distance", arguments=distance_args)
        sources = map_mcp_result(
            mapper="amap_distance",
            provider=definition.provider,
            category=definition.category,
            display_name=definition.display_name,
            query=query,
            raw=response.raw,
        )
        for source in sources:
            source.metadata.update(
                {
                    "origins": origins,
                    "destination": destination_text,
                    "origin_locations": origin_locations,
                    "destination_location": destination_location,
                    "mode": mode,
                    "mcp_tool_name": "maps_distance",
                }
            )
            source.display_text = self._format_distance_display(
                source=source,
                origins=[str(item) for item in origins[:8]],
                destination=destination_text,
            )
        return sources, self._mcp_metadata("amap_distance_measure", "maps_distance", distance_args)

    async def _run_amap_poi_workflow(
        self,
        *,
        client: McpHttpClient,
        definition: ToolDefinition,
        call: PlannedToolCall,
        arguments: dict[str, Any],
    ) -> tuple[list[ExternalSource], dict[str, Any]]:
        query = str(arguments.get("query") or call.arguments.get("query") or "")
        keyword = str(arguments.get("keyword") or AmapToolProvider._extract_map_keyword(query) or query).strip()
        anchor = str(arguments.get("location") or "").strip()
        if anchor:
            geo = await self._mcp_amap_geo(client=client, address=anchor)
            location = self._extract_amap_location(geo)
            if location:
                radius = int(arguments.get("radius") or 3000)
                poi_args = {"keywords": keyword, "location": location, "radius": str(radius)}
                response = await client.call_tool(tool_name="maps_around_search", arguments=poi_args)
                sources = map_mcp_result(
                    mapper="amap_map",
                    provider=definition.provider,
                    category=definition.category,
                    display_name=f"{definition.display_name}周边",
                    query=query,
                    raw=response.raw,
                )
                for source in sources:
                    source.metadata.update({"keyword": keyword, "anchor": anchor, "mcp_tool_name": "maps_around_search"})
                return sources, self._mcp_metadata("amap_poi_search", "maps_around_search", poi_args)

        response = await client.call_tool(tool_name="maps_text_search", arguments={"keywords": keyword})
        sources = map_mcp_result(
            mapper="amap_map",
            provider=definition.provider,
            category=definition.category,
            display_name=f"{definition.display_name}搜索",
            query=query,
            raw=response.raw,
        )
        for source in sources:
            source.metadata.update({"keyword": keyword, "mcp_tool_name": "maps_text_search"})
        return sources, self._mcp_metadata("amap_poi_search", "maps_text_search", {"keywords": keyword})

    async def _resolve_two_locations(
        self,
        *,
        client: McpHttpClient,
        origin: str,
        destination: str,
    ) -> tuple[dict[str, Any], dict[str, Any], str, str]:
        origin_geo = await self._mcp_amap_geo(client=client, address=origin)
        destination_geo = await self._mcp_amap_geo(client=client, address=destination)
        origin_location = self._extract_amap_location(origin_geo)
        destination_location = self._extract_amap_location(destination_geo)
        if not origin_location or not destination_location:
            raise RuntimeError("高德 MCP 地理编码未返回有效坐标。")
        return origin_geo, destination_geo, origin_location, destination_location

    async def _mcp_amap_geo(self, *, client: McpHttpClient, address: str) -> dict[str, Any]:
        response = await client.call_tool(tool_name="maps_geo", arguments={"address": address})
        payload = self._extract_mcp_payload(response.raw)
        return payload if isinstance(payload, dict) else {"payload": payload}

    @staticmethod
    def _extract_mcp_payload(raw: dict[str, Any]) -> Any:
        result = raw.get("result") or raw
        content = result.get("content") if isinstance(result, dict) else None
        if isinstance(content, list) and content:
            for item in content:
                if isinstance(item, dict) and item.get("text"):
                    text = str(item["text"])
                    if text.strip().startswith(("{", "[")):
                        try:
                            return json.loads(text)
                        except json.JSONDecodeError:
                            return {"text": text}
                    return {"text": text}
        return result

    @staticmethod
    def _extract_amap_location(payload: dict[str, Any]) -> str:
        candidates: list[Any] = [payload.get("location"), payload.get("lnglat"), payload.get("coordinates")]
        geocodes = payload.get("geocodes")
        if isinstance(geocodes, list) and geocodes:
            first = geocodes[0] if isinstance(geocodes[0], dict) else {}
            candidates.extend([first.get("location"), first.get("lnglat"), first.get("coordinates")])
        results = payload.get("results")
        if isinstance(results, list) and results:
            first = results[0] if isinstance(results[0], dict) else {}
            candidates.extend([first.get("location"), first.get("lnglat"), first.get("coordinates")])
        text = str(payload.get("text") or "")
        if text:
            match = re.search(r"(\d{2,3}\.\d+,\d{1,2}\.\d+)", text)
            if match:
                return match.group(1)
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return ""

    @staticmethod
    def _extract_amap_city(payload: dict[str, Any]) -> str:
        for key in ("city", "province"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        geocodes = payload.get("geocodes")
        if isinstance(geocodes, list) and geocodes and isinstance(geocodes[0], dict):
            for key in ("city", "province"):
                value = geocodes[0].get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        results = payload.get("results")
        if isinstance(results, list) and results and isinstance(results[0], dict):
            for key in ("city", "province"):
                value = results[0].get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        return ""

    @staticmethod
    def _extract_amap_adcode_or_city(payload: dict[str, Any]) -> str:
        for key in ("adcode", "district", "city"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        geocodes = payload.get("geocodes")
        if isinstance(geocodes, list) and geocodes and isinstance(geocodes[0], dict):
            for key in ("adcode", "district", "city"):
                value = geocodes[0].get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        results = payload.get("results")
        if isinstance(results, list) and results and isinstance(results[0], dict):
            for key in ("adcode", "district", "city"):
                value = results[0].get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
        text = str(payload.get("text") or "")
        match = re.search(r"(?:adcode|adCode|行政区划代码)[：:\"]+\s*(\d{6})", text)
        if match:
            return match.group(1)
        return ""

    async def _run_python(
        self,
        *,
        definition: ToolDefinition,
        call: PlannedToolCall,
        api_key: str | None,
    ) -> tuple[list[ExternalSource], dict[str, Any]]:
        method = str(definition.adapter.get("python_method") or "")
        return await self._run_python_method(method=method, call=call, api_key=api_key)

    async def _run_python_method(
        self,
        *,
        method: str,
        call: PlannedToolCall,
        api_key: str | None,
    ) -> tuple[list[ExternalSource], dict[str, Any]]:
        query = str(call.arguments.get("query") or "")
        if method == "tavily.query":
            sources = await self.tavily_provider.query(query, api_key=api_key)
        elif method == "amap.query_weather":
            sources = await self.amap_provider.query_weather(query, api_key=api_key)
        elif method == "amap.query_map":
            sources = await self.amap_provider.query_map(query, api_key=api_key)
        else:
            raise RuntimeError(f"未知 Python 工具方法：{method}")
        return sources, {"adapter_type": "python", "python_method": method}

    @staticmethod
    def _build_adapter_arguments(*, definition: ToolDefinition, call: PlannedToolCall) -> dict[str, Any]:
        arguments: dict[str, Any] = {}
        for key, value in (definition.adapter.get("default_arguments") or {}).items():
            arguments[key] = value
        argument_map = definition.adapter.get("argument_map") or {}
        if not argument_map:
            return {**arguments, **call.arguments}
        for source_key, target_key in argument_map.items():
            if source_key in call.arguments and call.arguments[source_key] not in (None, ""):
                arguments[str(target_key)] = call.arguments[source_key]
        return arguments

    @staticmethod
    def _mcp_metadata(workflow: str, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "adapter_type": "mcp_http",
            "mcp_workflow": workflow,
            "mcp_tool_name": tool_name,
            "mcp_arguments": arguments,
        }

    @staticmethod
    def _format_distance_display(*, source: ExternalSource, origins: list[str], destination: str) -> str:
        raw = source.metadata.get("raw")
        results = raw.get("results") if isinstance(raw, dict) and isinstance(raw.get("results"), list) else []
        if not results and isinstance(raw, dict) and raw.get("distance"):
            results = [raw]
        if not results:
            return source.display_text

        rows: list[tuple[str, float | None, str, str]] = []
        for index, item in enumerate(results, start=1):
            if not isinstance(item, dict):
                continue
            origin_name = origins[index - 1] if index - 1 < len(origins) else f"起点 {index}"
            distance_value = ToolAdapterRunner._parse_float(item.get("distance"))
            duration = ToolAdapterRunner._format_seconds(item.get("duration"))
            distance = ToolAdapterRunner._format_meters(item.get("distance"))
            rows.append((origin_name, distance_value, distance, duration))

        if not rows:
            return source.display_text
        lines = [f"{origin} -> {destination}：距离 {distance}，预计耗时 {duration}" for origin, _, distance, duration in rows]
        comparable = [(origin, distance) for origin, distance, _, _ in rows if distance is not None]
        if len(comparable) >= 2:
            nearest_origin, nearest_distance = min(comparable, key=lambda item: item[1])
            lines.append(f"结论：按高德返回的距离，{nearest_origin} 更近，约 {ToolAdapterRunner._format_meters(nearest_distance)}。")
        return "\n".join(lines)

    @staticmethod
    def _parse_float(value: Any) -> float | None:
        try:
            return float(str(value or "").strip())
        except ValueError:
            return None

    @staticmethod
    def _format_meters(value: Any) -> str:
        meters = ToolAdapterRunner._parse_float(value)
        if meters is None:
            return str(value or "未知")
        if meters >= 1000:
            return f"{meters / 1000:.1f} 公里"
        return f"{int(meters)} 米"

    @staticmethod
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
