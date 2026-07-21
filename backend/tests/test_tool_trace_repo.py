from __future__ import annotations

import unittest

from app.repositories.tool_trace_repo import ToolTraceRepository


class ToolTraceRepositoryTest(unittest.TestCase):
    def test_call_status_and_sources_are_bound_by_call_id(self) -> None:
        events = [
            {
                "type": "tool_call_error",
                "call_id": "denied",
                "tool_key": "amap.maps.weather",
                "provider": "amap",
                "category": "weather",
                "status": "skipped",
                "error": "missing credential",
            },
            {
                "type": "tool_call_start",
                "call_id": "success",
                "tool_key": "amap.maps.weather",
                "provider": "amap",
                "category": "weather",
                "arguments": {"city": "深圳"},
            },
            {
                "type": "tool_call_end",
                "call_id": "success",
                "tool_key": "amap.maps.weather",
                "provider": "amap",
                "category": "weather",
                "status": "success",
                "sources_count": 1,
            },
        ]
        sources = [
            {
                "provider": "amap",
                "source_type": "weather",
                "metadata": {"call_id": "success"},
            }
        ]

        runs = ToolTraceRepository._build_call_runs("route", events, sources)
        by_id = {run.call_id: run for run in runs}

        self.assertEqual(by_id["denied"].status, "skipped")
        self.assertEqual(by_id["denied"].sources_count, 0)
        self.assertEqual(by_id["success"].status, "success")
        self.assertEqual(by_id["success"].sources_count, 1)


if __name__ == "__main__":
    unittest.main()
