from __future__ import annotations

import asyncio
import unittest

from app.services.tools.providers.amap import AmapToolProvider


class FakeAmapWeatherProvider(AmapToolProvider):
    def __init__(self) -> None:
        super().__init__(api_key="test-key")
        self.weather_calls: list[str] = []
        self.resolved_locations: list[str] = []

    async def _request_amap_weather(self, city: str) -> dict:
        self.weather_calls.append(city)
        if city == "深圳松岗":
            return {"status": "1", "info": "OK", "lives": []}
        if city == "440306":
            return {
                "status": "1",
                "info": "OK",
                "lives": [
                    {
                        "province": "广东",
                        "city": "宝安区",
                        "weather": "多云",
                        "temperature": "28",
                        "winddirection": "东南",
                        "windpower": "≤3",
                        "humidity": "70",
                        "reporttime": "2026-05-29 12:00:00",
                    }
                ],
            }
        return {"status": "1", "info": "OK", "lives": []}

    async def _resolve_weather_city_candidates(self, location: str) -> list[str]:
        self.resolved_locations.append(location)
        return ["440306", "宝安区", "深圳市"]


class AmapProviderTest(unittest.TestCase):
    def test_extract_route_query_supports_distance_phrasing(self) -> None:
        cases = [
            (
                "深圳松岗离汕头市潮阳区西凤村多远",
                ("深圳松岗", "汕头市潮阳区西凤村", "driving"),
            ),
            (
                "深圳松岗和汕头市潮阳区西凤村相距多少公里",
                ("深圳松岗", "汕头市潮阳区西凤村", "driving"),
            ),
            (
                "深圳松岗到汕头市潮阳区西凤村开车多久",
                ("深圳松岗", "汕头市潮阳区西凤村", "driving"),
            ),
            (
                "深圳松岗到汕头市潮阳区西凤村步行多久",
                ("深圳松岗", "汕头市潮阳区西凤村", "walking"),
            ),
        ]

        for query, expected in cases:
            with self.subTest(query=query):
                self.assertEqual(AmapToolProvider._extract_route_query(query), expected)

    def test_weather_falls_back_from_street_location_to_adcode(self) -> None:
        async def run_test() -> None:
            provider = FakeAmapWeatherProvider()

            sources = await provider.query_weather("深圳松岗天气怎么样")

            self.assertEqual(provider.weather_calls, ["深圳松岗", "440306"])
            self.assertEqual(provider.resolved_locations, ["深圳松岗"])
            self.assertEqual(len(sources), 1)
            self.assertEqual(sources[0].source_type, "weather")
            self.assertIn("宝安区当前天气：多云", sources[0].display_text)
            self.assertEqual(sources[0].metadata["requested_location"], "深圳松岗")
            self.assertEqual(sources[0].metadata["resolved_city"], "440306")

        asyncio.run(run_test())


if __name__ == "__main__":
    unittest.main()
