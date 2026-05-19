from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import settings


@dataclass
class ExternalSource:
    source_type: str
    provider: str
    title: str
    display_text: str
    url: str | None = None
    rank: int | None = None
    score: float | None = None
    used_in_prompt: bool = True
    citation_label: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExternalContextResult:
    context_text: str | None
    sources: list[ExternalSource]
    notices: list[str]
    diagnostics: dict[str, Any]
    details: dict[str, Any]


class RuleBasedToolRouter:
    WEATHER_PATTERN = re.compile(r"(天气|气温|温度|下雨|降雨|台风|空气质量|冷不冷|热不热)")
    MAP_PATTERN = re.compile(r"(附近|路线|怎么去|地址|导航|公交|驾车|步行|地铁|周边)")

    def route(self, query: str) -> str:
        if self.WEATHER_PATTERN.search(query):
            return "weather"
        if self.MAP_PATTERN.search(query):
            # 地图工具预留，第一版先回退到网页搜索，避免输出不完整路线。
            return "web_search"
        return "web_search"


class ExternalContextService:
    def __init__(self) -> None:
        self.router = RuleBasedToolRouter()

    async def build_context(self, *, query: str, enabled: bool, max_chars: int) -> ExternalContextResult:
        if not enabled:
            return ExternalContextResult(
                context_text=None,
                sources=[],
                notices=[],
                diagnostics={
                    "external_context_enabled": 0,
                    "external_tool_called": "none",
                    "external_sources_total": 0,
                    "external_sources_included": 0,
                    "external_context_chars": 0,
                },
                details={"external_sources": []},
            )

        started = time.perf_counter()
        tool = self.router.route(query)
        notices: list[str] = []
        sources: list[ExternalSource] = []
        error_message = ""

        try:
            if tool == "weather":
                sources = await self._query_amap_weather(query)
                if not sources:
                    notices.append("天气工具未返回有效结果，已回退到网页搜索。")
                    sources = await self._query_tavily(query)
                    tool = "web_search"
            else:
                sources = await self._query_tavily(query)
        except Exception as exc:
            error_message = str(exc)
            notices.append(f"外部信息工具调用失败：{error_message}")

        elapsed_ms = int((time.perf_counter() - started) * 1000)
        context_text = self._format_sources_for_prompt(sources, max_chars=max_chars)
        included_sources = sources if context_text else []
        public_sources = [source.to_public_dict() for source in sources]

        return ExternalContextResult(
            context_text=context_text,
            sources=sources,
            notices=notices,
            diagnostics={
                "external_context_enabled": 1,
                "external_tool_called": tool,
                "external_sources_total": len(sources),
                "external_sources_included": len(included_sources),
                "external_context_chars": len(context_text or ""),
                "external_context_latency_ms": elapsed_ms,
                "external_context_error": int(bool(error_message)),
            },
            details={"external_sources": public_sources},
        )

    async def _query_tavily(self, query: str) -> list[ExternalSource]:
        api_key = settings.tavily_api_key.strip()
        if not api_key:
            raise RuntimeError("未配置 TAVILY_API_KEY")

        payload = {
            "api_key": api_key,
            "query": query,
            "search_depth": "basic",
            "include_answer": True,
            "include_raw_content": False,
            "max_results": 5,
        }
        async with httpx.AsyncClient(timeout=settings.external_tool_timeout_seconds) as client:
            response = await client.post("https://api.tavily.com/search", json=payload)
            response.raise_for_status()
            data = response.json()

        sources: list[ExternalSource] = []
        answer = (data.get("answer") or "").strip()
        if answer:
            sources.append(
                ExternalSource(
                    source_type="web",
                    provider="tavily",
                    title="Tavily 综合摘要",
                    display_text=answer,
                    rank=0,
                    citation_label="[S]",
                )
            )

        for index, item in enumerate(data.get("results", []) or [], start=1):
            url = (item.get("url") or "").strip() or None
            title = (item.get("title") or "").strip() or (urlparse(url or "").netloc or "搜索结果")
            content = (item.get("content") or item.get("snippet") or "").strip()
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
                    metadata={"domain": urlparse(url or "").netloc},
                )
            )
        return sources

    async def _query_amap_weather(self, query: str) -> list[ExternalSource]:
        api_key = settings.amap_api_key.strip()
        if not api_key:
            raise RuntimeError("未配置 AMAP_API_KEY")

        city = self._extract_city(query)
        params = {
            "key": api_key,
            "city": city,
            "extensions": "base",
            "output": "JSON",
        }
        async with httpx.AsyncClient(timeout=settings.external_tool_timeout_seconds) as client:
            response = await client.get("https://restapi.amap.com/v3/weather/weatherInfo", params=params)
            response.raise_for_status()
            data = response.json()

        lives = data.get("lives") or []
        if not lives:
            info = data.get("info") or "无天气结果"
            raise RuntimeError(f"高德天气查询失败：{info}")

        sources: list[ExternalSource] = []
        for index, item in enumerate(lives, start=1):
            province = item.get("province") or ""
            city_name = item.get("city") or city
            weather = item.get("weather") or "未知"
            temperature = item.get("temperature") or "未知"
            wind_direction = item.get("winddirection") or "未知"
            wind_power = item.get("windpower") or "未知"
            humidity = item.get("humidity") or "未知"
            report_time = item.get("reporttime") or "未知"
            display = (
                f"{province}{city_name}当前天气：{weather}，气温 {temperature} 摄氏度，"
                f"{wind_direction}风 {wind_power} 级，湿度 {humidity}%，发布时间 {report_time}。"
            )
            sources.append(
                ExternalSource(
                    source_type="weather",
                    provider="amap",
                    title=f"{city_name}天气",
                    display_text=display,
                    rank=index,
                    citation_label=f"[W{index}]",
                    metadata={
                        "city": city_name,
                        "province": province,
                        "reporttime": report_time,
                    },
                )
            )
        return sources

    @staticmethod
    def _extract_city(query: str) -> str:
        cleaned = re.sub(r"(今天|明天|后天|现在|当前|最近|请问|帮我|查一下|查询)", "", query)
        match = re.search(r"([\u4e00-\u9fa5]{2,10}?)(?:的)?(?:天气|气温|温度|下雨|降雨|空气质量)", cleaned)
        if match:
            return match.group(1)
        return "广州"

    @staticmethod
    def _format_sources_for_prompt(sources: list[ExternalSource], *, max_chars: int) -> str | None:
        if not sources:
            return None

        parts: list[str] = []
        used_chars = 0
        for index, source in enumerate(sources, start=1):
            label = source.citation_label or f"[{index}]"
            url_line = f"\nURL: {source.url}" if source.url else ""
            text = (
                f"{label} 类型：{source.source_type}\n"
                f"来源：{source.provider}\n"
                f"标题：{source.title}{url_line}\n"
                f"内容：{source.display_text.strip()}"
            ).strip()
            remaining = max_chars - used_chars
            if remaining <= 0:
                break
            if len(text) > remaining:
                text = text[:remaining].rstrip() + "\n[已按上下文预算截断]"
            parts.append(text)
            used_chars += len(text)

        if not parts:
            return None

        return (
            "以下是本轮按用户授权调用外部信息工具得到的结果。"
            "涉及实时信息时请优先参考这些来源；引用网页或工具结果时请使用来源编号。\n\n"
            + "\n\n".join(parts)
        )
