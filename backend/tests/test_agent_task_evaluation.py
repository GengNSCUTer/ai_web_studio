"""Agent评测分层统计的确定性测试，不调用模型或工具。"""

import unittest

from app.services.agent_task_evaluation import facts_present, inspect_trace, report, summarize


class AgentTaskEvaluationTest(unittest.TestCase):
    def test_candidate_and_planner_denominator_are_separate(self):
        trace = inspect_trace(
            [{"type": "tool_candidate_selection", "candidates": [{"tool_key": "amap.maps.weather"}]},
             {"type": "tool_plan", "plan": {"calls": [{"tool_key": "amap.maps.weather"}]}}],
            [{"tool_key": "amap.maps.weather", "status": "success"}],
            [["amap.maps.weather"], ["amap.maps.direction.driving"]],
        )
        self.assertFalse(trace["candidate_complete"])
        self.assertEqual(trace["candidate_group_hits"], 1)
        self.assertEqual(trace["planner_group_hits"], 1)
        payload = report({"name": "x", "version": 1, "target_quotas": {"single_tool": 1}, "annotation": "x", "cases": []}, [
            {"id": "x", "category": "single_tool", "runner": "chat", "status": "failed", "elapsed_ms": 2, "trace": trace}
        ])
        self.assertEqual(payload["summary"]["candidate_tool_group_recall"]["numerator"], 1)
        self.assertIsNone(payload["summary"]["planner_tool_group_coverage_given_candidates"]["value"])

    def test_done_transport_and_tool_success_do_not_force_business_pass(self):
        row = {"id": "x", "category": "single_tool", "runner": "chat", "status": "partial", "elapsed_ms": 10,
               "trace": {"trace_available": True, "candidate_events_available": True, "required_groups": 1,
                         "candidate_group_hits": 1, "candidate_complete": True, "planner_group_hits": 1,
                         "forbidden_candidate_keys": [], "schema_passed": 1, "schema_total": 1,
                         "quality_valid": 0, "quality_total": 1, "call_statuses": {"success": 1}}}
        self.assertEqual(summarize([row])["task_success"]["value"], 0)
        self.assertEqual(summarize([row])["quality_pass"]["value"], 0)

    def test_facts_are_grouped_with_equivalents(self):
        self.assertEqual(facts_present("五月毛利为4,000元，约增加10%", [["4000", "4,000"], ["10%", "10 %"]]), [True, True])

    def test_missing_trace_is_not_zero_quality(self):
        row = {"id": "x", "category": "no_tool", "runner": "chat", "status": "failed", "elapsed_ms": 1,
               "trace": {"trace_available": False, "candidate_events_available": False, "required_groups": 0,
                         "candidate_group_hits": 0, "candidate_complete": False, "planner_group_hits": 0,
                         "forbidden_candidate_keys": [], "schema_passed": 0, "schema_total": 0,
                         "quality_valid": 0, "quality_total": 0, "call_statuses": {}}}
        summary = summarize([row])
        self.assertEqual(summary["missing_candidate_trace_cases"], 1)
        self.assertIsNone(summary["parameter_schema_validity"]["value"])


if __name__ == "__main__":
    unittest.main()
