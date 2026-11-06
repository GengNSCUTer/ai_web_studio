"""汇总固定业务回归/独立题/资源证据；不重跑任务，也不替失败改分。"""

import argparse
from collections import Counter
import json
import math
from pathlib import Path


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)] if ordered else None


def evidence(report):
    rows = [row for row in report["cases"] if row["status"] != "skipped"]
    stats = [row["runtime_stats"] for row in rows if row.get("runtime_stats")]
    phases = ("chat_prepare_orchestrator", "context_orchestrator", "chat_stream_orchestrator")
    failed = Counter(key for row in rows for key, value in row.get("checks", {}).items() if value is False)
    modes = {}
    snapshot = {row["id"]: row for row in report["dataset_snapshot"]["cases"]}
    for row in rows:
        group = row["runner"] + ":" + snapshot[row["id"]].get("mode", "unknown")
        item = modes.setdefault(group, {"rows": [], "latencies": [], "rounds": [], "calls": [], "stops": Counter()})
        item["rows"].append(row)
        item["latencies"].append(row.get("task_elapsed_ms", row["elapsed_ms"]))
        runtime = row.get("runtime_stats", {})
        budget = runtime.get("external_tool_run_budget", {})
        if budget:
            item["rounds"].append(budget["planning_rounds_used"])
            item["calls"].append(budget["tool_calls_used"])
            item["stops"][runtime.get("external_agent_terminal_reason", "unknown")] += 1
    grouped = {}
    for mode, item in modes.items():
        grouped[mode] = {"cases": len(item["rows"]), "statuses": dict(Counter(row["status"] for row in item["rows"])),
            "p50_ms": percentile(item["latencies"], .5), "p95_ms": percentile(item["latencies"], .95),
            "planner_rounds": dict(Counter(item["rounds"])), "tool_calls": dict(Counter(item["calls"])),
            "stop_reasons": dict(item["stops"])}
    usage = [row for row in rows if row.get("final_answer_input_tokens") is not None]
    return {"summary": report["summary"], "by_category": report["by_category"], "by_runner": report["by_runner"],
        "graph_coverage": {"chat_metric_rows": len(stats), **{phase: sum(row.get(phase) == "langgraph" for row in stats)
            for phase in phases}, "tool_graph_trace_rows": sum(any(e.get("type") == "tool_graph_node" for e in row.get("events", [])) for row in rows)},
        "mode_distribution": grouped, "failed_check_counts": dict(failed),
        "uncompleted": [{"id": row["id"], "status": row["status"],
            "failed_checks": [k for k, v in row.get("checks", {}).items() if v is False],
            "error_type": row.get("error_type"), "stop_reason": row.get("runtime_stats", {}).get("external_agent_terminal_reason")}
            for row in rows if row["status"] != "passed"],
        "final_answer_usage": {"cases_with_usage": len(usage),
            "input_tokens": sum(row["final_answer_input_tokens"] for row in usage),
            "output_tokens": sum(row.get("final_answer_output_tokens") or 0 for row in usage),
            "complete_task_tokens": None, "note": "仅最终回答Provider用量；Planner/Embedding/重排/文档草稿不齐全，不计算总成本"},
        "browser": report.get("browser"), "cleanup": report.get("cleanup")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("baseline", "current", "holdout", "resources", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("不能覆盖原报告")
    old, current, holdout, resources = [json.loads(path.read_text()) for path in (
        args.baseline, args.current, args.holdout, args.resources)]
    if resources.get("status") != "completed":
        parser.error("资源采样未完成，不能用中间快照生成最终汇总")
    for report in (current, holdout):
        if report.get("status") != "executed_results" or any(row.get("skip_reason") == "not_run" for row in report["cases"]):
            parser.error("任务仍在执行，不生成最终汇总")
    same_cases = old["dataset_snapshot"]["cases"] == current["dataset_snapshot"]["cases"]
    same_inputs = old["dataset_snapshot"]["fixtures"] == current["dataset_snapshot"]["fixtures"]
    old_rows = {row["id"]: row for row in old["cases"]}
    delta = [{"id": row["id"], "before": old_rows[row["id"]]["status"], "now": row["status"]}
             for row in current["cases"] if row["id"] in old_rows and row["status"] != old_rows[row["id"]]["status"]]
    process_summary = {}
    for name in resources["samples"][0]["processes"] if resources["samples"] else ():
        samples = [sample["processes"][name] for sample in resources["samples"]]
        process_summary[name] = {"pids": sorted({sample["pid"] for sample in samples}),
            **{field: {"first": samples[0][field], "last": samples[-1][field], "max": max(sample[field] for sample in samples)}
               for field in ("rss_kib", "fds", "threads")}}
    payload = {"scope": "迁移后固定200任务和12题留出回归；不是同期A/B或生产SLA，模型随机性和前序业务修复均影响结果",
        "same_case_annotations": same_cases, "same_fixture_inputs": same_inputs,
        "baseline_reference": old["summary"], "current": evidence(current), "holdout": evidence(holdout),
        "case_status_changes": delta, "resource_samples": len(resources["samples"]), "process_resources": process_summary,
        "health_sample_failures": sum(any(code != 200 for code in sample["health"].values()) for sample in resources["samples"]),
        "sampling_failures": resources.get("sampling_failures", []),
        "max_idle_in_transaction": max((sample["pg_sessions"].get("idle in transaction", 0) for sample in resources["samples"]), default=None),
        "max_idle_transaction_seconds": max((sample["max_idle_transaction_seconds"] for sample in resources["samples"]
            if "max_idle_transaction_seconds" in sample), default=None),
        "sources": {name: str(getattr(args, name)) for name in ("baseline", "current", "holdout", "resources")}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"current": payload["current"]["summary"]["statuses"], "holdout": payload["holdout"]["summary"]["statuses"],
        "same_cases": same_cases, "same_inputs": same_inputs, "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
