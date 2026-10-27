"""评测分母和层级回归：调用成功不能替代用户任务完成。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"scripts"))
from evaluate_tool_tasks import coverage, summarize


class ToolEvaluationMetricsTests(unittest.TestCase):
    def test_partial_task_stays_failed_even_when_all_http_calls_succeed(self):
        rows=[dict(group="file",passed=True,elapsed_ms=100,candidate_covered=True,planner_covered=True,
                   schema_pass=2,schema_total=2,calls=[dict(status="success")]),
              dict(group="multi",passed=False,elapsed_ms=200,candidate_covered=True,planner_covered=False,
                   schema_pass=1,schema_total=2,calls=[dict(status="success")]),
              dict(group="negative",passed=False,elapsed_ms=300,no_tool_correct=False,calls=[])]
        metrics=summarize(rows,[dict(passed=True),dict(passed=False)])
        self.assertAlmostEqual(metrics["task_success_rate"],1/3)
        self.assertEqual(metrics["execution_success_rate"],1)
        self.assertEqual(metrics["candidate_task_coverage"],1)
        self.assertEqual(metrics["planner_task_coverage"],.5)
        self.assertEqual(metrics["argument_schema_pass_rate"],.75)
        self.assertEqual(metrics["safety_rejected"],1)

    def test_dependency_task_requires_all_required_capabilities(self):
        required=[["workspace.files.search","workspace.files.read"],["amap.maps.weather"]]
        self.assertFalse(coverage(required,["workspace.files.search"]))
        self.assertTrue(coverage(required,["workspace.files.read","amap.maps.weather"]))

    def test_no_tools_only_has_no_positive_recall_denominator(self):
        metrics=summarize([dict(group="negative",passed=True,elapsed_ms=10,no_tool_correct=True)],[])
        self.assertIsNone(metrics["candidate_task_coverage"])
        self.assertIsNone(metrics["argument_schema_pass_rate"])
        self.assertIsNone(metrics["execution_success_rate"])


if __name__=="__main__":unittest.main()
