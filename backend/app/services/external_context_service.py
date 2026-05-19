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
    MAP_PATTERN = re.compile(r"(附近|路线|怎么去|怎么走|地址|导航|公交|驾车|开车|步行|地铁|周边|位置|在哪|哪里|地图|距离|行政区)")

    def route(self, query: str) -> str:
        if self.WEATHER_PATTERN.search(query):
            return "weather"
        if self.MAP_PATTERN.search(query):
            return "map"
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
            elif tool == "map":
                sources = await self._query_amap_map(query)
                if not sources:
                    notices.append("高德基础地图工具未返回有效结果，已回退到网页搜索。")
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

    async def _query_amap_map(self, query: str) -> list[ExternalSource]:
        api_key = settings.amap_api_key.strip()
        if not api_key:
            raise RuntimeError("未配置 AMAP_API_KEY")

        district_keyword = self._extract_district_keyword(query)
        if district_keyword:
            return await self._query_amap_district(district_keyword)

        route = self._extract_route_query(query)
        if route:
            origin_text, destination_text, mode = route
            origin = await self._query_amap_geocode_one(origin_text)
            destination = await self._query_amap_geocode_one(destination_text)
            if origin and destination:
                return await self._query_amap_route(
                    origin_text=origin_text,
                    destination_text=destination_text,
                    origin=origin,
                    destination=destination,
                    mode=mode,
                )

        keyword = self._extract_map_keyword(query)
        if keyword:
            poi_query = self._extract_poi_query(query)
            if poi_query:
                anchor, poi_keyword = poi_query
                if anchor:
                    anchor_geo = await self._query_amap_geocode_one(anchor)
                    anchor_location = self._clean_amap_scalar(anchor_geo.get("location")) if anchor_geo else ""
                    if anchor_location:
                        sources = await self._query_amap_poi_around(
                            anchor=anchor,
                            location=anchor_location,
                            keyword=poi_keyword,
                        )
                        if sources:
                            return sources
                sources = await self._query_amap_poi_text(keyword=poi_keyword, city=self._extract_city_for_map(query))
                if sources:
                    return sources

            if self._looks_like_place_lookup(query):
                sources = await self._query_amap_poi_text(keyword=keyword, city=self._extract_city_for_map(query))
                if sources:
                    return sources

            sources = await self._query_amap_geocode(keyword)
            return sources

        return []

    async def _query_amap_geocode(self, keyword: str) -> list[ExternalSource]:
        data = await self._request_amap_json(
            "https://restapi.amap.com/v3/geocode/geo",
            {
                "address": keyword,
                "output": "JSON",
            },
        )
        geocodes = data.get("geocodes") or []
        sources: list[ExternalSource] = []
        for index, item in enumerate(geocodes[:3], start=1):
            location = self._clean_amap_scalar(item.get("location"))
            formatted_address = self._clean_amap_scalar(item.get("formatted_address")) or keyword
            province = self._clean_amap_scalar(item.get("province"))
            city = self._clean_amap_scalar(item.get("city"))
            district = self._clean_amap_scalar(item.get("district"))
            adcode = self._clean_amap_scalar(item.get("adcode"))
            level = self._clean_amap_scalar(item.get("level"))
            display = (
                f"地点：{formatted_address}\n"
                f"坐标：{location or '未知'}\n"
                f"行政区：{province or ''}{city or ''}{district or ''}\n"
                f"adcode：{adcode or '未知'}\n"
                f"匹配级别：{level or '未知'}"
            ).strip()
            sources.append(
                ExternalSource(
                    source_type="map",
                    provider="amap",
                    title=f"高德地理编码：{formatted_address}",
                    url=self._build_amap_marker_url(location, formatted_address),
                    display_text=display,
                    rank=index,
                    citation_label=f"[M{index}]",
                    metadata={
                        "tool": "map.geocode",
                        "query": keyword,
                        "location": location,
                        "formatted_address": formatted_address,
                        "province": province,
                        "city": city,
                        "district": district,
                        "adcode": adcode,
                        "level": level,
                    },
                )
            )
        return sources

    async def _query_amap_poi_text(self, *, keyword: str, city: str | None) -> list[ExternalSource]:
        params = {
            "keywords": keyword,
            "offset": "8",
            "page": "1",
            "extensions": "base",
            "output": "JSON",
        }
        if city:
            params["city"] = city
            params["citylimit"] = "false"
        data = await self._request_amap_json("https://restapi.amap.com/v3/place/text", params)
        return self._format_amap_pois(
            data.get("pois") or [],
            title_prefix=f"高德POI搜索：{keyword}",
            query=keyword,
            tool="map.poi.text",
        )

    async def _query_amap_poi_around(self, *, anchor: str, location: str, keyword: str) -> list[ExternalSource]:
        data = await self._request_amap_json(
            "https://restapi.amap.com/v3/place/around",
            {
                "location": location,
                "keywords": keyword,
                "radius": "3000",
                "offset": "8",
                "page": "1",
                "extensions": "base",
                "output": "JSON",
            },
        )
        sources = self._format_amap_pois(
            data.get("pois") or [],
            title_prefix=f"高德周边搜索：{anchor}附近{keyword}",
            query=f"{anchor}附近{keyword}",
            tool="map.poi.around",
        )
        for source in sources:
            source.metadata["anchor"] = anchor
            source.metadata["anchor_location"] = location
        return sources

    def _format_amap_pois(
        self,
        pois: list[dict[str, Any]],
        *,
        title_prefix: str,
        query: str,
        tool: str,
    ) -> list[ExternalSource]:
        sources: list[ExternalSource] = []
        for index, item in enumerate(pois[:8], start=1):
            name = self._clean_amap_scalar(item.get("name")) or "未知地点"
            location = self._clean_amap_scalar(item.get("location"))
            address = self._clean_amap_scalar(item.get("address"))
            type_name = self._clean_amap_scalar(item.get("type"))
            province = self._clean_amap_scalar(item.get("pname"))
            city = self._clean_amap_scalar(item.get("cityname"))
            district = self._clean_amap_scalar(item.get("adname"))
            distance = self._format_meters(item.get("distance")) if self._clean_amap_scalar(item.get("distance")) else ""
            tel = self._clean_amap_scalar(item.get("tel"))
            display = (
                f"名称：{name}\n"
                f"类型：{type_name or '未知'}\n"
                f"地址：{address or '未知'}\n"
                f"行政区：{province}{city}{district}\n"
                f"坐标：{location or '未知'}"
                + (f"\n距离：{distance}" if distance else "")
                + (f"\n电话：{tel}" if tel else "")
            )
            sources.append(
                ExternalSource(
                    source_type="map",
                    provider="amap",
                    title=f"{title_prefix} - {name}",
                    url=self._build_amap_marker_url(location, name),
                    display_text=display,
                    rank=index,
                    citation_label=f"[M{index}]",
                    metadata={
                        "tool": tool,
                        "query": query,
                        "id": self._clean_amap_scalar(item.get("id")),
                        "name": name,
                        "location": location,
                        "address": address,
                        "type": type_name,
                        "province": province,
                        "city": city,
                        "district": district,
                        "distance": self._clean_amap_scalar(item.get("distance")),
                    },
                )
            )
        return sources

    async def _query_amap_geocode_one(self, keyword: str) -> dict[str, Any] | None:
        data = await self._request_amap_json(
            "https://restapi.amap.com/v3/geocode/geo",
            {
                "address": keyword,
                "output": "JSON",
            },
        )
        geocodes = data.get("geocodes") or []
        return geocodes[0] if geocodes else None

    async def _query_amap_route(
        self,
        *,
        origin_text: str,
        destination_text: str,
        origin: dict[str, Any],
        destination: dict[str, Any],
        mode: str,
    ) -> list[ExternalSource]:
        origin_location = self._clean_amap_scalar(origin.get("location"))
        destination_location = self._clean_amap_scalar(destination.get("location"))
        if not origin_location or not destination_location:
            return []

        if mode == "walking":
            endpoint = "https://restapi.amap.com/v3/direction/walking"
        elif mode == "transit":
            endpoint = "https://restapi.amap.com/v3/direction/transit/integrated"
        else:
            endpoint = "https://restapi.amap.com/v3/direction/driving"

        params: dict[str, str] = {
            "origin": origin_location,
            "destination": destination_location,
            "output": "JSON",
        }
        if mode == "transit":
            city = self._clean_amap_scalar(origin.get("city")) or self._clean_amap_scalar(origin.get("province")) or ""
            cityd = (
                self._clean_amap_scalar(destination.get("city"))
                or self._clean_amap_scalar(destination.get("province"))
                or city
            )
            if city:
                params["city"] = city
            if cityd:
                params["cityd"] = cityd

        data = await self._request_amap_json(endpoint, params)
        route = data.get("route") or {}
        if mode == "transit":
            return self._format_amap_transit_route(
                route=route,
                origin_text=origin_text,
                destination_text=destination_text,
                origin_location=origin_location,
                destination_location=destination_location,
            )
        return self._format_amap_simple_route(
            route=route,
            mode=mode,
            origin_text=origin_text,
            destination_text=destination_text,
            origin_location=origin_location,
            destination_location=destination_location,
        )

    async def _query_amap_district(self, keyword: str) -> list[ExternalSource]:
        data = await self._request_amap_json(
            "https://restapi.amap.com/v3/config/district",
            {
                "keywords": keyword,
                "subdistrict": "1",
                "extensions": "base",
                "output": "JSON",
            },
        )
        districts = data.get("districts") or []
        sources: list[ExternalSource] = []
        for index, item in enumerate(districts[:3], start=1):
            name = self._clean_amap_scalar(item.get("name")) or keyword
            adcode = self._clean_amap_scalar(item.get("adcode"))
            center = self._clean_amap_scalar(item.get("center"))
            level = self._clean_amap_scalar(item.get("level"))
            children = item.get("districts") or []
            child_names = [
                self._clean_amap_scalar(child.get("name"))
                for child in children[:12]
                if self._clean_amap_scalar(child.get("name"))
            ]
            display = (
                f"行政区：{name}\n"
                f"adcode：{adcode or '未知'}\n"
                f"中心点：{center or '未知'}\n"
                f"级别：{level or '未知'}\n"
                f"下级区域示例：{'、'.join(child_names) if child_names else '无'}"
            )
            sources.append(
                ExternalSource(
                    source_type="map",
                    provider="amap",
                    title=f"高德行政区域：{name}",
                    url=self._build_amap_marker_url(center, name),
                    display_text=display,
                    rank=index,
                    citation_label=f"[M{index}]",
                    metadata={
                        "tool": "map.district",
                        "query": keyword,
                        "name": name,
                        "adcode": adcode,
                        "center": center,
                        "level": level,
                        "child_count": len(children),
                    },
                )
            )
        return sources

    async def _request_amap_json(self, endpoint: str, params: dict[str, str]) -> dict[str, Any]:
        api_key = settings.amap_api_key.strip()
        request_params = {"key": api_key, **params}
        async with httpx.AsyncClient(timeout=settings.external_tool_timeout_seconds) as client:
            response = await client.get(endpoint, params=request_params)
            response.raise_for_status()
            data = response.json()

        if str(data.get("status")) != "1":
            info = data.get("info") or data.get("infocode") or "未知错误"
            raise RuntimeError(f"高德地图查询失败：{info}")
        return data

    def _format_amap_simple_route(
        self,
        *,
        route: dict[str, Any],
        mode: str,
        origin_text: str,
        destination_text: str,
        origin_location: str,
        destination_location: str,
    ) -> list[ExternalSource]:
        paths = route.get("paths") or []
        if not paths:
            return []

        mode_label = "步行" if mode == "walking" else "驾车"
        path = paths[0]
        distance = self._format_meters(path.get("distance"))
        duration = self._format_seconds(path.get("duration"))
        steps = path.get("steps") or []
        instructions = [
            self._clean_amap_scalar(step.get("instruction"))
            for step in steps[:8]
            if self._clean_amap_scalar(step.get("instruction"))
        ]
        display = (
            f"{mode_label}路线：{origin_text} -> {destination_text}\n"
            f"起点坐标：{origin_location}\n"
            f"终点坐标：{destination_location}\n"
            f"距离：{distance}\n"
            f"预计耗时：{duration}\n"
            f"路线步骤：{'；'.join(instructions) if instructions else '高德未返回详细步骤'}"
        )
        return [
            ExternalSource(
                source_type="map",
                provider="amap",
                title=f"高德{mode_label}路线：{origin_text}到{destination_text}",
                url=self._build_amap_navigation_url(
                    origin_location=origin_location,
                    destination_location=destination_location,
                    destination_name=destination_text,
                    mode=mode,
                ),
                display_text=display,
                rank=1,
                citation_label="[M1]",
                metadata={
                    "tool": f"map.route.{mode}",
                    "origin": origin_text,
                    "destination": destination_text,
                    "origin_location": origin_location,
                    "destination_location": destination_location,
                    "distance": self._clean_amap_scalar(path.get("distance")),
                    "duration": self._clean_amap_scalar(path.get("duration")),
                    "step_count": len(steps),
                },
            )
        ]

    def _format_amap_transit_route(
        self,
        *,
        route: dict[str, Any],
        origin_text: str,
        destination_text: str,
        origin_location: str,
        destination_location: str,
    ) -> list[ExternalSource]:
        transits = route.get("transits") or []
        if not transits:
            return []

        transit = transits[0]
        distance = self._format_meters(transit.get("distance") or route.get("distance"))
        duration = self._format_seconds(transit.get("duration"))
        walking_distance = self._format_meters(transit.get("walking_distance"))
        lines: list[str] = []
        for segment in (transit.get("segments") or [])[:8]:
            bus = segment.get("bus") or {}
            buslines = bus.get("buslines") or []
            for busline in buslines[:1]:
                name = self._clean_amap_scalar(busline.get("name"))
                departure = self._clean_amap_scalar((busline.get("departure_stop") or {}).get("name"))
                arrival = self._clean_amap_scalar((busline.get("arrival_stop") or {}).get("name"))
                if name:
                    lines.append(f"{name}：{departure or '未知站'} -> {arrival or '未知站'}")
        display = (
            f"公共交通路线：{origin_text} -> {destination_text}\n"
            f"起点坐标：{origin_location}\n"
            f"终点坐标：{destination_location}\n"
            f"距离：{distance}\n"
            f"预计耗时：{duration}\n"
            f"步行距离：{walking_distance}\n"
            f"主要线路：{'；'.join(lines) if lines else '高德未返回公交/地铁线路明细'}"
        )
        return [
            ExternalSource(
                source_type="map",
                provider="amap",
                title=f"高德公交路线：{origin_text}到{destination_text}",
                url=self._build_amap_navigation_url(
                    origin_location=origin_location,
                    destination_location=destination_location,
                    destination_name=destination_text,
                    mode="transit",
                ),
                display_text=display,
                rank=1,
                citation_label="[M1]",
                metadata={
                    "tool": "map.route.transit",
                    "origin": origin_text,
                    "destination": destination_text,
                    "origin_location": origin_location,
                    "destination_location": destination_location,
                    "distance": self._clean_amap_scalar(transit.get("distance") or route.get("distance")),
                    "duration": self._clean_amap_scalar(transit.get("duration")),
                    "walking_distance": self._clean_amap_scalar(transit.get("walking_distance")),
                    "segment_count": len(transit.get("segments") or []),
                },
            )
        ]

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
    def _extract_route_query(query: str) -> tuple[str, str, str] | None:
        compact = re.sub(r"\s+", "", query.strip())
        patterns = [
            r"从(.+?)到(.+?)(?:怎么走|怎么去|路线|导航|驾车|开车|步行|公交|地铁|公共交通|$)",
            r"(.+?)到(.+?)(?:怎么走|怎么去|路线|导航|驾车|开车|步行|公交|地铁|公共交通)",
        ]
        for pattern in patterns:
            match = re.search(pattern, compact)
            if not match:
                continue
            origin = ExternalContextService._clean_place_text(match.group(1))
            destination = ExternalContextService._clean_place_text(match.group(2))
            if not origin or not destination or origin == destination:
                continue
            mode = "driving"
            if re.search(r"(步行|走路)", compact):
                mode = "walking"
            elif re.search(r"(公交|地铁|公共交通)", compact):
                mode = "transit"
            return origin, destination, mode
        return None

    @staticmethod
    def _extract_district_keyword(query: str) -> str | None:
        if not re.search(r"(行政区|行政区域|区划|下辖|下级区域)", query):
            return None
        cleaned = re.sub(r"(请问|帮我|查一下|查询|一下|的|行政区|行政区域|区划|下辖|下级区域|有哪些|是什么)", "", query)
        cleaned = ExternalContextService._clean_place_text(cleaned)
        return cleaned or None

    @staticmethod
    def _extract_map_keyword(query: str) -> str | None:
        compact = re.sub(r"\s+", "", query.strip())
        nearby_match = re.search(r"(.+?)(?:附近|周边)", compact)
        if nearby_match:
            return ExternalContextService._clean_place_text(nearby_match.group(1))

        cleaned = re.sub(
            r"(请问|帮我|查一下|查询|一下|地图|位置|地址|在哪里|在哪|哪里|怎么去|怎么走|导航|路线|附近|周边|距离|有多远|显示|打开|的)",
            "",
            compact,
        )
        return ExternalContextService._clean_place_text(cleaned) or None

    @staticmethod
    def _extract_poi_query(query: str) -> tuple[str | None, str] | None:
        compact = re.sub(r"\s+", "", query.strip())
        poi_keyword_pattern = r"(咖啡店|咖啡|酒店|宾馆|餐厅|饭店|商场|医院|银行|地铁站|停车场|景点|超市|便利店|药店|公园|充电站|加油站)"
        nearby_match = re.search(rf"(.+?)(?:附近|周边)(?:的|有|有没有|有什么)?(.+)?", compact)
        if nearby_match:
            anchor = ExternalContextService._clean_place_text(nearby_match.group(1))
            raw_keyword = nearby_match.group(2) or ""
            keyword_match = re.search(poi_keyword_pattern, raw_keyword)
            keyword = keyword_match.group(1) if keyword_match else ExternalContextService._clean_place_text(raw_keyword)
            if not keyword:
                keyword = "兴趣点"
            return anchor or None, keyword

        keyword_match = re.search(poi_keyword_pattern, compact)
        if keyword_match:
            city = ExternalContextService._extract_city_for_map(compact)
            return None, f"{city or ''}{keyword_match.group(1)}"
        return None

    @staticmethod
    def _extract_city_for_map(query: str) -> str | None:
        match = re.search(r"([\u4e00-\u9fa5]{2,8}?)(?:市|省)?(?:附近|周边|咖啡|酒店|餐厅|饭店|商场|医院|银行|地铁|景点|超市|行政区|路线|地址)", query)
        if match:
            city = match.group(1)
            if city not in {"附近", "周边", "路线", "地址", "查询", "请问", "帮我"}:
                return city
        return None

    @staticmethod
    def _looks_like_place_lookup(query: str) -> bool:
        return bool(re.search(r"(在哪里|在哪|哪里|位置|地址|地图)", query))

    @staticmethod
    def _clean_place_text(value: str) -> str:
        cleaned = re.sub(r"[？?。！!，,；;：:]", "", value or "")
        cleaned = re.sub(r"(请问|帮我|查询|查一下|一下|从|到)$", "", cleaned)
        cleaned = re.sub(r"(坐|乘坐|搭乘|地铁|公交|公共交通|开车|驾车|步行|走路)$", "", cleaned)
        return cleaned.strip()

    @staticmethod
    def _clean_amap_scalar(value: Any) -> str:
        if value is None or isinstance(value, list):
            return ""
        return str(value).strip()

    @staticmethod
    def _format_meters(value: Any) -> str:
        raw = ExternalContextService._clean_amap_scalar(value)
        try:
            meters = float(raw)
        except ValueError:
            return raw or "未知"
        if meters >= 1000:
            return f"{meters / 1000:.1f} 公里"
        return f"{int(meters)} 米"

    @staticmethod
    def _format_seconds(value: Any) -> str:
        raw = ExternalContextService._clean_amap_scalar(value)
        try:
            seconds = int(float(raw))
        except ValueError:
            return raw or "未知"
        if seconds >= 3600:
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            return f"{hours} 小时 {minutes} 分钟"
        if seconds >= 60:
            return f"{seconds // 60} 分钟"
        return f"{seconds} 秒"

    @staticmethod
    def _build_amap_marker_url(location: str | None, name: str) -> str | None:
        if not location:
            return None
        return f"https://uri.amap.com/marker?position={location}&name={name}"

    @staticmethod
    def _build_amap_navigation_url(
        *,
        origin_location: str,
        destination_location: str,
        destination_name: str,
        mode: str,
    ) -> str:
        mode_map = {
            "walking": "walk",
            "transit": "bus",
            "driving": "car",
        }
        amap_mode = mode_map.get(mode, "car")
        return (
            "https://uri.amap.com/navigation"
            f"?from={origin_location},起点"
            f"&to={destination_location},{destination_name}"
            f"&mode={amap_mode}"
        )

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
