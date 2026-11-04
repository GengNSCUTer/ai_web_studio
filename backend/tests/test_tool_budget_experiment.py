"""防止预算实验把传输成功当任务成功，或从分母删除请求错误。"""

import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/evaluate_tool_budget_experiment.py"
SPEC = importlib.util.spec_from_file_location("budget_experiment", SCRIPT)
experiment = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(experiment)


class ToolBudgetExperimentTests(unittest.TestCase):
    def test_weather_accepts_markdown_and_requires_actual_live_source(self):
        source = {"source_type": "weather", "metadata": {"raw": {
            "city": "深圳市", "weather_data_type": "live", "temperature": "27",
        }}}
        checks = experiment.check_task("weather", "深圳天气", "气温 **`27`** 摄氏度", [source], [], True)
        self.assertTrue(all(checks.values()))
        self.assertFalse(all(experiment.check_task("weather", "深圳天气", "27摄氏度", [], [], True).values()))

    def test_done_answer_without_real_diff_is_not_complete(self):
        checks = experiment.check_task("edit_proposal", "标题修改", "这是预览，已改为评测笔记", [], [], True)
        self.assertFalse(checks["real_diff_preview"])

    def test_request_error_stays_in_denominator(self):
        rows = [{"mode": "quick_chat", "case": "weather", "error_type": "ReadTimeout"}]
        metric = experiment.summarize(rows)["quick_chat"]
        self.assertEqual(metric["samples"], 1)
        self.assertEqual(metric["request_errors"], 1)
        self.assertEqual(metric["task_statuses"]["failed"], 1)
        self.assertEqual(metric["full_answer_rate"], 0)
        self.assertIsNone(metric["p95_total_ms"])

    def test_comparison_requires_both_files_and_both_facts(self):
        calls = [{"tool_key": "workspace.files.read", "status": "success"}]
        sources = [{"source_type": "workspace_file_read", "metadata": {"file_id": "one"}}]
        checks = experiment.check_task("file_compare", "对比", "CedarLedger 17 days 02:30 03:10", sources, calls, True)
        self.assertFalse(checks["two_files_read"])


if __name__ == "__main__":
    unittest.main()
