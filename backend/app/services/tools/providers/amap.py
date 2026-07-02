from __future__ import annotations

import re
from typing import Any

import httpx

from app.core.config import settings
from app.services.tools.schemas import ExternalSource


class AmapToolProvider:
    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key

    def _api_key(self) -> str:
        return (self.api_key or "").strip()

    async def query_map(self, query: str, *, api_key: str | None = None) -> list[ExternalSource]:
        if api_key is not None:
            self.api_key = api_key
        api_key = self._api_key()
        if not api_key:
            raise RuntimeError("未配置高德地图 API Key")

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

    async def query_weather(self, query: str, *, api_key: str | None = None) -> list[ExternalSource]:
        if api_key is not None:
            self.api_key = api_key
        api_key = self._api_key()
        if not api_key:
            raise RuntimeError("未配置高德地图 API Key")

        requested_location = self._extract_city(query)
        tried_cities: list[str] = []

        direct_data = await self._request_amap_weather(requested_location)
        tried_cities.append(requested_location)
        lives = direct_data.get("lives") or []
        resolved_city = requested_location

        if not lives:
            for candidate in await self._resolve_weather_city_candidates(requested_location):
                if not candidate or candidate in tried_cities:
                    continue
                tried_cities.append(candidate)
                fallback_data = await self._request_amap_weather(candidate)
                lives = fallback_data.get("lives") or []
                if lives:
                    resolved_city = candidate
                    break

        if not lives:
            raise RuntimeError(f"高德天气查询无结果：{requested_location}（已尝试：{'、'.join(tried_cities)}）")

        return self._format_weather_sources(
            lives,
            requested_location=requested_location,
            resolved_city=resolved_city,
        )

    async def _request_amap_weather(self, city: str) -> dict[str, Any]:
        params = {
            "city": city,
            "extensions": "base",
            "output": "JSON",
        }
        async with httpx.AsyncClient(timeout=settings.external_tool_timeout_seconds) as client:
            response = await client.get(
                "https://restapi.amap.com/v3/weather/weatherInfo",
                params={"key": self._api_key(), **params},
            )
            response.raise_for_status()
            data = response.json()

        if str(data.get("status")) != "1":
            info = data.get("info") or data.get("infocode") or "未知错误"
            raise RuntimeError(f"高德天气查询失败：{info}")
        return data

    async def _resolve_weather_city_candidates(self, location: str) -> list[str]:
        candidates: list[str] = []
        geocode = await self._query_amap_geocode_one(location)
        if geocode:
            for key in ("adcode", "district", "city"):
                value = self._clean_amap_scalar(geocode.get(key))
                if value and value not in candidates:
                    candidates.append(value)

        # 高德天气接口要求城市/区县级 city 或 adcode；街道级地点常返回 OK 但 lives 为空。
        compact = re.sub(r"\s+", "", location)
        if compact.endswith(("街道", "镇", "乡")) and len(compact) > 2:
            stripped = re.sub(r"(街道|镇|乡)$", "", compact)
            if stripped and stripped not in candidates:
                candidates.append(stripped)
        return candidates

    def _format_weather_sources(
        self,
        lives: list[dict[str, Any]],
        *,
        requested_location: str,
        resolved_city: str,
    ) -> list[ExternalSource]:
        sources: list[ExternalSource] = []
        for index, item in enumerate(lives, start=1):
            province = item.get("province") or ""
            city_name = item.get("city") or requested_location
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
                        "requested_location": requested_location,
                        "resolved_city": resolved_city,
                        "weather": weather,
                        "temperature": temperature,
                        "winddirection": wind_direction,
                        "windpower": wind_power,
                        "humidity": humidity,
                        "reporttime": report_time,
                    },
                )
            )
        return sources

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
        api_key = self._api_key()
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

    @staticmethod
    def _extract_city(query: str) -> str:
        cleaned = re.sub(r"(今天|明天|后天|现在|当前|最近|请问|帮我|查一下|查询)", "", query)
        match = re.search(r"([\u4e00-\u9fa5]{2,10}?)(?:的)?(?:天气|气温|温度|下雨|降雨|空气质量)", cleaned)
        if match:
            city = AmapToolProvider._extract_city_name_from_place(match.group(1))
            if city:
                return city
        return "广州"

    @staticmethod
    def _extract_route_query(query: str) -> tuple[str, str, str] | None:
        compact = re.sub(r"\s+", "", query.strip())
        patterns = [
            r"(.+?)分别到(.+?)(?:有)?(?:多远|多少公里|几公里|要多久|多久|开车多久|步行多久)",
            r"从(.+?)到(.+?)(?:怎么走|怎么去|路线|导航|驾车|开车|步行|公交|地铁|公共交通|$)",
            r"(.+?)到(.+?)(?:路上|沿途|途中)(?:有|有哪些|有什么)?",
            r"(.+?)到(.+?)(?:预计耗时|耗时|多久到)",
            r"(.+?)到(.+?)(?:怎么走|怎么去|路线|导航|驾车|开车|步行|公交|地铁|公共交通)",
            r"(.+?)(?:离|距离)(.+?)(?:有)?(?:多远|多少公里|几公里|要多久|多久|开车多久|步行多久)",
            r"(.+?)(?:和|与|跟)(.+?)(?:相距|距离)(?:多远|多少公里|几公里|多久)?",
            r"(.+?)到(.+?)(?:有)?(?:多远|多少公里|几公里|要多久|多久|开车多久|步行多久)",
        ]
        for pattern in patterns:
            match = re.search(pattern, compact)
            if not match:
                continue
            origin = AmapToolProvider._clean_place_text(match.group(1))
            destination = AmapToolProvider._clean_place_text(match.group(2))
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
        cleaned = AmapToolProvider._clean_place_text(cleaned)
        return cleaned or None

    @staticmethod
    def _extract_map_keyword(query: str) -> str | None:
        compact = re.sub(r"\s+", "", query.strip())
        nearby_match = re.search(r"(.+?)(?:附近|周边)", compact)
        if nearby_match:
            return AmapToolProvider._clean_place_text(nearby_match.group(1))

        cleaned = re.sub(
            r"(请问|帮我|查一下|查询|一下|地图|位置|地址|在哪里|在哪|哪里|怎么去|怎么走|导航|路线|附近|周边|距离|有多远|显示|打开|的)",
            "",
            compact,
        )
        return AmapToolProvider._clean_place_text(cleaned) or None

    @staticmethod
    def _extract_poi_query(query: str) -> tuple[str | None, str] | None:
        compact = re.sub(r"\s+", "", query.strip())
        poi_keyword_pattern = r"(咖啡店|咖啡|酒店|宾馆|餐厅|饭店|商场|医院|银行|地铁站|停车场|景点|超市|便利店|药店|公园|充电站|加油站)"
        nearby_match = re.search(rf"(.+?)(?:附近|周边)(?:的|有|有没有|有什么)?(.+)?", compact)
        if nearby_match:
            anchor = AmapToolProvider._clean_place_text(nearby_match.group(1))
            raw_keyword = nearby_match.group(2) or ""
            keyword_match = re.search(poi_keyword_pattern, raw_keyword)
            keyword = keyword_match.group(1) if keyword_match else AmapToolProvider._clean_place_text(raw_keyword)
            if not keyword:
                keyword = "兴趣点"
            return anchor or None, keyword

        keyword_match = re.search(poi_keyword_pattern, compact)
        if keyword_match:
            city = AmapToolProvider._extract_city_for_map(compact)
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
        cleaned = re.sub(r"(路上|沿途|途中|有哪些|有什么|服务区|预计耗时|耗时|天气|顺便看).*$", "", cleaned)
        return cleaned.strip()

    @staticmethod
    def _extract_city_name_from_place(value: str) -> str:
        cleaned = AmapToolProvider._clean_place_text(value)
        if not cleaned:
            return ""
        match = re.search(r"([\u4e00-\u9fa5]{2,8}?市)", cleaned)
        if match:
            return match.group(1)
        match = re.search(r"([\u4e00-\u9fa5]{2,6})(?:区|县|镇|街道|站|村|机场|港口|服务区)", cleaned)
        if match:
            return match.group(1)
        if len(cleaned) <= 6 and re.fullmatch(r"[\u4e00-\u9fa5]+", cleaned):
            return cleaned
        return cleaned[:8]

    @staticmethod
    def _clean_amap_scalar(value: Any) -> str:
        if value is None or isinstance(value, list):
            return ""
        return str(value).strip()

    @staticmethod
    def _format_meters(value: Any) -> str:
        raw = AmapToolProvider._clean_amap_scalar(value)
        try:
            meters = float(raw)
        except ValueError:
            return raw or "未知"
        if meters >= 1000:
            return f"{meters / 1000:.1f} 公里"
        return f"{int(meters)} 米"

    @staticmethod
    def _format_seconds(value: Any) -> str:
        raw = AmapToolProvider._clean_amap_scalar(value)
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
