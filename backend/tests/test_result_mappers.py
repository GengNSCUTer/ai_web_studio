from __future__ import annotations

import unittest
import json

from app.services.tools.quality import evaluate_tool_result_quality
from app.services.tools.adapters import ToolAdapterRunner
from app.services.tools.mcp_client import McpCallResponse
from app.services.tools.result_mappers import extract_mcp_payload, map_mcp_result


class ProviderResultMapperTest(unittest.TestCase):
    def test_amap_argument_normalizer_reads_structured_content(self) -> None:
        class FakeClient:
            async def call_tool(self, *, tool_name, arguments, output_schema=None):
                self.last_tool_name = tool_name
                return McpCallResponse(
                    raw={
                        "result": {
                            "structuredContent": {
                                "results": [
                                    {"location": "113.324521,23.106428", "city": "广州市"}
                                ]
                            },
                            "content": [{"type": "text", "text": '{"results": []}'}],
                        }
                    }
                )

        async def run_test() -> None:
            client = FakeClient()
            normalized = await ToolAdapterRunner()._normalize_raw_amap_arguments(
                client=client,
                mcp_tool_name="maps_direction_driving",
                arguments={"origin": "广州塔", "destination": "113.36,23.12"},
            )
            self.assertEqual(normalized["origin"], "113.324521,23.106428")
            self.assertEqual(normalized["destination"], "113.36,23.12")

        import asyncio

        asyncio.run(run_test())

    def test_structured_content_has_priority_over_text_compatibility_copy(self) -> None:
        raw = {
            "result": {
                "structuredContent": {
                    "results": [
                        {
                            "formatted_address": "广州市天河区",
                            "location": "113.33,23.13",
                        }
                    ]
                },
                "content": [{"type": "text", "text": '{"results": []}'}],
            }
        }

        payload = extract_mcp_payload(raw)

        self.assertEqual(payload["results"][0]["location"], "113.33,23.13")

    def test_text_json_payload_is_extracted_after_provider_note(self) -> None:
        raw = {
            "result": {
                "content": [
                    {"type": "text", "text": "provider note: results follow"},
                    {
                        "type": "text",
                        "text": json.dumps(
                            {
                                "results": [
                                    {
                                        "url": "https://example.com/evidence",
                                        "title": "证据页",
                                        "content": "可引用的正文内容",
                                    }
                                ]
                            },
                            ensure_ascii=False,
                        ),
                    },
                ]
            }
        }

        payload = extract_mcp_payload(raw)
        sources = map_mcp_result(
            mapper="tavily_search",
            provider="tavily",
            category="web_search",
            display_name="Tavily",
            query="测试",
            raw=raw,
        )

        self.assertIsInstance(payload, dict)
        self.assertEqual(payload["results"][0]["title"], "证据页")
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].metadata["raw"]["url"], "https://example.com/evidence")

    def test_tavily_mapper_keeps_only_citable_results(self) -> None:
        raw = {
            "result": {
                "structuredContent": {
                    "answer": "摘要不能替代网页来源",
                    "results": [
                        {
                            "url": "https://example.com/a",
                            "title": "结果 A",
                            "content": "第一条证据 API_KEY=remote-secret-value " + "x" * 2_000,
                            "score": 0.91,
                            "raw_content": "不应保存的大段正文",
                        },
                        {"title": "缺少 URL 的结果", "content": "不能引用"},
                    ],
                }
            }
        }

        sources = map_mcp_result(
            mapper="tavily_search",
            provider="tavily",
            category="web_search",
            display_name="Tavily",
            query="测试",
            raw=raw,
        )

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].metadata["raw"]["url"], "https://example.com/a")
        self.assertNotIn("raw_content", sources[0].metadata["raw"])
        self.assertNotIn("remote-secret-value", sources[0].display_text)
        self.assertNotIn("remote-secret-value", sources[0].metadata["raw"]["content"])
        self.assertLessEqual(len(sources[0].metadata["raw"]["content"]), 1600)
        self.assertEqual(
            evaluate_tool_result_quality(
                sources=sources,
                contract={
                    "semantic_profile": "web_search",
                    "profile_mapping": {
                        "evidence_paths": ["/sources/*/metadata/raw/content"],
                        "identity_paths": ["/sources/*/metadata/raw/url"],
                        "collection_paths": ["/sources"],
                        "item_evidence_paths": ["/metadata/raw/content"],
                        "item_identity_paths": ["/metadata/raw/url"],
                    },
                },
            ).status,
            "valid",
        )

    def test_amap_content_json_mappers_build_bounded_canonical_fields(self) -> None:
        cases = [
            (
                "amap_weather",
                {"city": "广州市", "forecasts": [{"date": "2026-09-14", "dayweather": "晴"}]},
                "weather",
                "晴",
            ),
            (
                "amap_geo",
                {"results": [{"formatted_address": "华南理工大学", "location": "113.35,23.16", "adcode": "440106"}]},
                "formatted_address",
                "华南理工大学",
            ),
            (
                "amap_distance",
                {"results": [{"origin_id": "1", "dest_id": "2", "distance": "1200", "duration": "300"}]},
                "distance",
                "1200",
            ),
            (
                "amap_route",
                {"origin": "113.3,23.1", "destination": "113.4,23.2", "paths": [{"distance": "1000", "duration": "240"}]},
                "origin",
                "113.3,23.1",
            ),
            (
                "amap_route",
                {"origin": "113.3,23.1", "destination": "113.4,23.2", "distance": "6500", "transits": [{"duration": "2500"}]},
                "duration",
                "2500",
            ),
            (
                "amap_poi",
                {"pois": [{"id": "p1", "name": "咖啡店", "address": "天河路", "location": "113.3,23.1"}]},
                "id",
                "p1",
            ),
        ]

        for mapper, payload, expected_key, expected_value in cases:
            with self.subTest(mapper=mapper):
                raw = {"result": {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}]}}
                sources = map_mcp_result(
                    mapper=mapper,
                    provider="amap",
                    category="map",
                    display_name="高德",
                    query="测试",
                    raw=raw,
                )
                self.assertTrue(sources)
                self.assertEqual(sources[0].metadata["raw"][expected_key], expected_value)

    def test_known_mapper_fails_closed_for_missing_business_fields(self) -> None:
        cases = {
            "amap_geo": {"results": [{"formatted_address": "只有地址"}]},
            "amap_weather": {"city": "广州", "forecasts": [{"date": "2026-09-14"}]},
            "amap_distance": {"results": [{"distance": "1200"}]},
            "amap_route": {"origin": "113.3,23.1", "paths": [{"distance": "1000"}]},
            "amap_poi": {"pois": [{"name": "没有 ID 的地点"}]},
        }

        for mapper, payload in cases.items():
            with self.subTest(mapper=mapper):
                sources = map_mcp_result(
                    mapper=mapper,
                    provider="amap",
                    category="map",
                    display_name="高德",
                    query="测试",
                    raw={"result": {"content": [{"type": "text", "text": json.dumps(payload)}]}},
                )
                self.assertEqual(sources, [])


if __name__ == "__main__":
    unittest.main()
