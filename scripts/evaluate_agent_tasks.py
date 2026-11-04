"""校验和分析Agent业务任务集；默认只做离线题本检查，不自动调用模型。

真实执行需要显式提供已保存的逐题结果JSON：
`--dataset backend/evals/agent_task_pilot_v1.json --results <results.json>`。
结果文件由受控运行脚本生成，不能把本脚本的validate-only当作任务通过。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.agent_task_evaluation import report, render_html  # noqa: E402


REQUIRED_CASE_KEYS = {"id", "category", "runner", "query", "expected"}
CATEGORIES = {"no_tool", "single_tool", "multi_tool", "knowledge_evidence", "file_approval", "fault_security"}
RUNNERS = {"chat", "document_task", "fault_injection"}


def validate_dataset(dataset: dict) -> list[str]:
    errors = []
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        errors.append("cases必须是非空数组")
        return errors
    ids = [case.get("id") for case in cases]
    if len(ids) != len(set(ids)):
        errors.append("case id重复")
    for index, case in enumerate(cases):
        missing = REQUIRED_CASE_KEYS - set(case)
        if missing:
            errors.append(f"第{index + 1}题缺字段:{','.join(sorted(missing))}")
        if case.get("category") not in CATEGORIES:
            errors.append(f"{case.get('id')}场景不在允许集合")
        if case.get("runner") not in RUNNERS:
            errors.append(f"{case.get('id')}runner不在允许集合")
        expected = case.get("expected") or {}
        if not isinstance(expected, dict):
            errors.append(f"{case.get('id')}expected必须是对象")
        if case.get("category") not in {"no_tool", "knowledge_evidence"} and not expected.get("required_tool_groups") and case.get("runner") == "chat":
            errors.append(f"{case.get('id')}工具任务缺少required_tool_groups")
        if case.get("category") == "file_approval" and case.get("runner") != "chat":
            errors.append(f"{case.get('id')}file_approval当前应由浏览器/Chat执行")
    return errors


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=ROOT / "backend/evals/agent_task_pilot_v1.json")
    parser.add_argument("--results", type=Path)
    parser.add_argument("--output", type=Path, default=ROOT / "backend/evals/results/agent_task_pilot_v1_validation.json")
    parser.add_argument("--html", type=Path)
    args = parser.parse_args()
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    errors = validate_dataset(dataset)
    if errors:
        payload = {"status": "invalid", "errors": errors, "dataset": dataset.get("name")}
    elif args.results:
        result_rows = json.loads(args.results.read_text(encoding="utf-8"))
        payload = report(dataset, result_rows)
        payload["status"] = "analyzed_results"
    else:
        payload = {
            "status": "validated_only",
            "dataset": dataset["name"],
            "available_cases": len(dataset["cases"]),
            "target_total": sum(dataset["target_quotas"].values()),
            "category_counts": {category: sum(case["category"] == category for case in dataset["cases"]) for category in sorted(CATEGORIES)},
            "note": "题本结构有效，但没有执行任务；不能把validated_only当作成功率。",
        }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    if args.html and payload.get("status") == "analyzed_results":
        args.html.write_text(render_html(payload), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    raise SystemExit(0 if payload["status"] != "invalid" else 2)


if __name__ == "__main__":
    main()
