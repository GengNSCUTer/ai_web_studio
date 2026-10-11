from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from app.repositories.tool_trace_repo import ToolTraceRepository
from app.services.tools.schemas import ExternalContextResult, ToolPlan, ToolTraceEvent


class ToolTraceRepositoryTest(unittest.TestCase):
    def test_public_trace_preserves_only_known_credential_source_states(self) -> None:
        for event_type in ("tool_policy_check", "tool_call_start"):
            with self.subTest(event_type=event_type):
                public = ToolTraceEvent(type=event_type, payload={
                    "credential_source": "user", "api_key": "private-test-key",
                    "arguments": {"Authorization": "Bearer private-test-key", "credential_source": "private-test-key"},
                }).to_public_dict()
                self.assertEqual(public["credential_source"], "user")
                self.assertEqual(public["api_key"], "***")
                self.assertEqual(public["arguments"]["Authorization"], "***")
                self.assertEqual(public["arguments"]["credential_source"], "***")
                self.assertNotIn("private-test-key", str(public))

    def test_public_trace_masks_unknown_source_values_and_remote_event_fields(self) -> None:
        for event_type, source in (
            ("tool_call_start", "private-test-key"),
            ("tool_policy_check", {"api_key": "private-test-key"}),
            ("remote_result", "user"),
        ):
            with self.subTest(event_type=event_type, source=source):
                public = ToolTraceEvent(type=event_type, payload={"credential_source": source}).to_public_dict()
                self.assertEqual(public["credential_source"], "***")

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

    def test_terminal_outcome_closes_timeout_and_dependency_records(self) -> None:
        runs = ToolTraceRepository._build_call_runs(
            "route",
            [
                {"type": "tool_call_start", "call_id": "slow", "tool_key": "web.tavily.search"},
                {
                    "type": "tool_workflow_step_outcome", "call_id": "slow",
                    "tool_key": "web.tavily.search", "execution_status": "timed_out",
                    "elapsed_ms": 50, "error_category": "tool_wall_clock_budget_exhausted",
                },
                {
                    "type": "tool_workflow_step_outcome", "call_id": "child",
                    "tool_key": "workspace.files.read", "execution_status": "blocked",
                    "elapsed_ms": 0, "error_category": "dependency_not_succeeded",
                },
            ],
            [],
        )
        by_id = {run.call_id: run for run in runs}
        self.assertEqual(by_id["slow"].status, "timed_out")
        self.assertIsNotNone(by_id["slow"].finished_at)
        self.assertEqual(by_id["child"].status, "skipped")
        self.assertEqual(by_id["child"].sources_count, 0)

    def test_first_planner_timeout_is_recorded_without_inventing_tool_calls(self) -> None:
        db = MagicMock()
        db.scalars.return_value.all.return_value = []
        result = ExternalContextResult(
            context_text=None, sources=[], notices=["规划超时"],
            diagnostics={"external_context_enabled": 1, "external_context_error": 1, "external_context_latency_ms": 80},
            details={}, tool_plan=None,
            tool_events=[ToolTraceEvent(type="tool_agent_budget_timeout", payload={"stage": "planner"})],
        )
        route = ToolTraceRepository(db).replace_for_assistant_message(
            user_id="user", conversation_id="conversation", user_message_id="question",
            assistant_message_id="answer", query="问题", external_context=result,
        )
        self.assertIsNotNone(route)
        self.assertEqual(route.router_type, "planner_incomplete")
        self.assertEqual(route.status, "error")
        self.assertEqual(route.plan_json, "{}")
        self.assertEqual(route.selected_tools_json, "[]")
        self.assertEqual(route.elapsed_ms, 80)
        self.assertIn("tool_agent_budget_timeout", route.events_json)
        db.commit.assert_called_once()

    def test_followup_planner_timeout_marks_route_partial_even_if_previous_plan_succeeded(self) -> None:
        db = MagicMock()
        db.scalars.return_value.all.return_value = []
        result = ExternalContextResult(
            context_text="已完成的证据", sources=[], notices=["续轮超时"],
            diagnostics={
                "external_context_enabled": 1, "external_context_error": 0,
                "external_tool_workflow_aggregate_status": "succeeded",
                "external_tool_next_action": "finalize_partial",
            },
            details={},
            tool_plan=ToolPlan(
                plan_id="first-plan", router="test", external_context_allowed=True,
                should_use_tools=True, calls=[],
            ),
        )
        route = ToolTraceRepository(db).replace_for_assistant_message(
            user_id="user", conversation_id="conversation", user_message_id="question",
            assistant_message_id="answer", query="问题", external_context=result,
        )
        self.assertEqual(route.status, "partial")


if __name__ == "__main__":
    unittest.main()
