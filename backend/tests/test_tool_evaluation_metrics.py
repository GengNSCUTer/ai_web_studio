"""评测分母和层级回归：调用成功不能替代用户任务完成。"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"scripts"))
from evaluate_tool_tasks import coverage, summarize, strict_checks


class ToolEvaluationMetricsTests(unittest.TestCase):
    def test_year_digits_do_not_count_as_observed_temperature(self):
        sources=[dict(source_type="weather",used_in_prompt=True,
            metadata={"raw":{"city":"深圳市","weather_data_type":"live","temperature":"26"}})]
        checks=strict_checks(dict(group="weather",query="深圳现在天气"),[],sources,"2026年，没有实况")
        self.assertFalse(checks["live_weather_深圳"])
        self.assertTrue(strict_checks(dict(group="weather",query="深圳现在天气"),[],sources,"深圳26℃")["live_weather_深圳"])

    def test_source_marker_and_third_party_page_are_not_official_link_delivery(self):
        source=dict(source_type="web",used_in_prompt=True,url="https://example.org/postgres")
        result=strict_checks(dict(group="search",query="PostgreSQL官方文档，给出来源链接"),[],[source],"资料[T1]")
        self.assertFalse(result["actual_source_url_in_answer"])
        self.assertFalse(result["requested_official_source"])

    def test_documentation_without_url_requirement_is_not_failed_for_plain_citations(self):
        result=strict_checks(dict(group="documentation",query="通过Context7查FastAPI的StreamingResponse用法"),[],
            [dict(source_type="documentation",used_in_prompt=True,url="https://fastapi.tiangolo.com/")],"用法说明[T1]")
        self.assertTrue(result["documentation_body"])
        self.assertNotIn("actual_source_url_in_answer",result)
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
