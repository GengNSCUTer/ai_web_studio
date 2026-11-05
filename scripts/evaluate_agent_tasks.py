"""默认仅校验题本；--execute运行真实业务，--results分析已有结果。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import re
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.services.agent_task_evaluation import report, render_html  # noqa: E402


REQUIRED_CASE_KEYS = {"id", "category", "business", "runner", "query", "expected"}
CATEGORIES = {"no_tool", "single_tool", "multi_tool", "knowledge_evidence", "file_approval", "fault_security"}
RUNNERS = {"chat", "document_task", "fault_injection"}


def validate_dataset(dataset: dict) -> list[str]:
    errors = []
    cases = dataset.get("cases")
    if not isinstance(cases, list) or not cases:
        errors.append("cases必须是非空数组")
        return errors
    if any(not isinstance(case, dict) for case in cases):
        return ['每题必须是对象']
    ids = [case.get("id") for case in cases]
    if any(not isinstance(case_id, str) or not case_id for case_id in ids):
        return ['case id必须是非空字符串']
    if any(not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', case_id) for case_id in ids):
        return ['case id只允许字母、数字、下划线和短横线，禁止产物路径越界']
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
        expected = case.get("expected")
        if not isinstance(expected, dict):
            errors.append(f"{case.get('id')}expected必须是对象")
            continue
        if not isinstance(case.get('query'), str) or not case['query'].strip():
            errors.append(f"{case.get('id')}query必须是非空字符串")
        if case.get("category") not in {"no_tool", "knowledge_evidence"} and not expected.get("required_tool_groups") and case.get("runner") == "chat":
            errors.append(f"{case.get('id')}工具任务缺少required_tool_groups")
        if case.get("category") == "file_approval" and case.get("runner") != "chat":
            errors.append(f"{case.get('id')}file_approval当前应由浏览器/Chat执行")
    return errors


def validate_results(dataset, rows):
    """结果必须精确对齐题本；缺题和重复题不能改变评测分母。"""
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        return ['结果必须是逐题对象数组']
    ids = [row.get('id') for row in rows]
    if any(not isinstance(case_id, str) for case_id in ids):
        return ['结果题号不合法']
    if len(ids) != len(set(ids)):
        return ['结果题号重复']
    targets = {case['id']: case for case in dataset['cases']}
    if set(ids) != set(targets):
        return ['结果存在缺题或未知题号，必须保留未执行项']
    for row in rows:
        target = targets[row['id']]
        if row.get('category') != target['category'] or row.get('runner') != target['runner']:
            return ['结果类别或执行链与题本不一致']
        if row.get('status') not in {'passed', 'partial', 'failed', 'skipped'}:
            return ['结果状态不合法']
    return []


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=ROOT / "backend/evals/agent_task_pilot_v1.json")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--results", type=Path)
    mode.add_argument('--execute', action='store_true')
    parser.add_argument("--output", type=Path)
    parser.add_argument("--html", type=Path)
    parser.add_argument('--base-url', default='http://127.0.0.1:32007')
    parser.add_argument('--email', default='1528713326@qq.com')
    parser.add_argument('--timeout', type=float, default=240)
    parser.add_argument('--browser-smoke', action='store_true')
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error('--timeout必须大于0')
    if args.output is None:
        name = 'agent-task-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        args.output = (ROOT / 'backend/evals/results' / name / 'report.json' if args.execute
                       else ROOT / 'backend/evals/results/agent_task_pilot_v1_validation.json')
    args.output = args.output.resolve()
    if args.execute and args.output.exists():
        parser.error('执行报告已存在，请用新目录保留每次运行，不能覆盖旧失败')
    dataset = json.loads(args.dataset.read_text(encoding="utf-8"))
    errors = validate_dataset(dataset)
    if errors:
        payload = {"status": "invalid", "errors": errors, "dataset": dataset.get("name")}
    elif args.execute:
        from agent_task_batch_runner import execute_batch
        payload = execute_batch(dataset, base_url=args.base_url, email=args.email, output=args.output,
                                html_path=(args.html or args.output.with_suffix('.html')).resolve(),
                                timeout=args.timeout, browser_smoke=args.browser_smoke)
        print(json.dumps({'status': payload['status'], 'summary': payload['summary'],
                          'cleanup': payload['cleanup'], 'report': str(args.output)}, ensure_ascii=False))
        success = (all(row['status'] == 'passed' for row in payload['cases'])
                   and payload['cleanup']['status'] == 'cleaned'
                   and (not args.browser_smoke or payload.get('browser', {}).get('status') == 'passed'))
        raise SystemExit(0 if success else 1)
    elif args.results:
        results = json.loads(args.results.read_text(encoding="utf-8"))
        result_rows = results.get('cases') if isinstance(results, dict) else results
        result_errors = validate_results(dataset, result_rows)
        if result_errors:
            payload = {'status': 'invalid', 'errors': result_errors}
        else:
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
        args.html.parent.mkdir(parents=True, exist_ok=True)
        args.html.write_text(render_html(payload), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))
    raise SystemExit(0 if payload["status"] != "invalid" else 2)


if __name__ == "__main__":
    main()
