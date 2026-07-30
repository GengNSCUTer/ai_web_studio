from __future__ import annotations

import json
import re
from typing import Any

from app.services.tools.mcp_client import McpHttpClient
from app.services.tools.mcp_security import enforce_mcp_endpoint_target_policy, validate_mcp_endpoint_url
from app.services.tools.providers.amap import AmapToolProvider
from app.services.tools.providers.workspace_files import WorkspaceFileToolProvider
from app.services.tools.result_mappers import map_mcp_result
from app.services.tools.schemas import ExternalSource, PlannedToolCall, ToolDefinition, redact_sensitive_arguments


class ToolAdapterRunner:
    def __init__(self, *, workspace_file_provider: WorkspaceFileToolProvider | None = None) -> None:
        self.workspace_file_provider = workspace_file_provider

    async def run(
        self,
        *,
        definition: ToolDefinition,
        call: PlannedToolCall,
        api_key: str | None,
    ) -> tuple[list[ExternalSource], dict[str, Any]]:
        if definition.adapter_type == "mcp_http":
            return await self._run_mcp_http(definition=definition, call=call, api_key=api_key)
        if definition.adapter_type == "workspace_file":
            if not self.workspace_file_provider:
                raise RuntimeError("工作区文件工具未初始化。")
            return await self.workspace_file_provider.run(call=call)
        raise RuntimeError(f"未知工具 adapter 类型：{definition.adapter_type}")

    async def _run_mcp_http(
        self,
        *,
        definition: ToolDefinition,
        call: PlannedToolCall,
        api_key: str | None,
    ) -> tuple[list[ExternalSource], dict[str, Any]]:
        endpoint_template = str(definition.adapter.get("endpoint_template") or "")
        mcp_tool_name = str(definition.adapter.get("mcp_tool_name") or "")
        auth_type = str(definition.adapter.get("auth_type") or "api_key").strip() or "api_key"
        if not endpoint_template or not mcp_tool_name:
            raise RuntimeError(f"工具 {definition.tool_key} 缺少 MCP endpoint 或 tool name。")
        needs_api_key = auth_type != "none" or "{api_key}" in endpoint_template
        if needs_api_key and not api_key:
            raise RuntimeError(f"工具 {definition.tool_key} 未配置 API Key。")

        validate_mcp_endpoint_url(endpoint_template, auth_type=auth_type)
        endpoint = endpoint_template.replace("{api_key}", api_key or "")
        if definition.source_type == "mcp_server":
            # 内置 manifest endpoint 由项目维护；用户动态添加的 Server 必须在每次调用前做 SSRF 检查。
            await enforce_mcp_endpoint_target_policy(endpoint)
        arguments = self._build_adapter_arguments(definition=definition, call=call)
        client = McpHttpClient(endpoint=endpoint, extra_headers=self._mcp_auth_headers(auth_type=auth_type, api_key=api_key))
        arguments = await self._normalize_raw_amap_arguments(client=client, mcp_tool_name=mcp_tool_name, arguments=arguments)

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
        return sources, {
            "adapter_type": "mcp_http",
            "mcp_tool_name": mcp_tool_name,
            "mcp_arguments": redact_sensitive_arguments(arguments),
        }

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
    def _build_adapter_arguments(*, definition: ToolDefinition, call: PlannedToolCall) -> dict[str, Any]:
        arguments: dict[str, Any] = {}
        for key, value in (definition.adapter.get("default_arguments") or {}).items():
            arguments[key] = value
        fixed_arguments = dict(definition.adapter.get("fixed_arguments") or {})
        argument_map = definition.adapter.get("argument_map") or {}
        if not argument_map:
            return {**arguments, **call.arguments, **fixed_arguments}
        for source_key, target_key in argument_map.items():
            if source_key in call.arguments and call.arguments[source_key] not in (None, ""):
                arguments[str(target_key)] = call.arguments[source_key]
        return {**arguments, **fixed_arguments}
