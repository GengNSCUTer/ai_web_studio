"""Agent评测离线分析：层次和分母显式，不以HTTP或done替代业务成功。"""

from collections import Counter
import html
import json
import math
import re


def ratio(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator,
            "value": numerator / denominator if denominator else None}


def facts_present(answer, groups):
    """等价词组逐组判定，仅用于预设有限事实，不等于整篇答案语义正确。"""
    plain = re.sub(r"[`*]", "", answer).casefold()
    return [any(re.search(r"(?<!\d)" + re.escape(term) + r"(?!\d)", plain) if term.isdigit()
                else term.casefold() in plain for term in group) for group in groups]


def inspect_trace(events, calls, required_groups, forbidden_keys=()):
    """现有Trace已扁平化；tool_plan内部仍有plan节点，两种层级不能混淆。"""
    selectors = [event for event in events if event.get("type") == "tool_candidate_selection"]
    candidate_keys = {item["tool_key"] for event in selectors for item in event.get("candidates", [])}
    plans = [event.get("plan", event) for event in events if event.get("type") == "tool_plan"]
    selected = {item["tool_key"] for plan in plans for item in plan.get("calls", [])}
    candidate_hits = sum(bool(candidate_keys.intersection(group)) for group in required_groups)
    selected_hits = sum(bool(selected.intersection(group)) for group in required_groups)
    schema = [event for event in events if event.get("type") == "tool_schema_validation"]
    quality = [event for event in events if event.get("type") == "tool_result_quality"]
    return {
        "trace_available": bool(events), "candidate_events_available": bool(selectors),
        "required_groups": len(required_groups), "candidate_group_hits": candidate_hits,
        "candidate_complete": bool(required_groups and selectors and candidate_hits == len(required_groups)),
        "planner_group_hits": selected_hits,
        "candidate_keys": sorted(candidate_keys), "planned_keys": sorted(selected),
        "forbidden_candidate_keys": sorted(candidate_keys.intersection(forbidden_keys)),
        "schema_passed": sum(event.get("status") == "passed" for event in schema),
        "schema_total": sum(event.get("status") in {"passed", "failed"} for event in schema),
        "quality_valid": sum(event.get("status") == "valid" for event in quality),
        "quality_total": sum(event.get("status") in {"valid", "uncertain", "invalid"} for event in quality),
        "call_statuses": dict(Counter(call["status"] for call in calls)),
        "fallback_count": sum(event.get("type") == "tool_fallback" for event in events),
    }


def classify(checks, critical=()):
    if not checks or any(checks.get(key) is not True for key in critical):
        return "failed"
    if all(value is True for value in checks.values()):
        return "passed"
    return "partial" if any(value is True for value in checks.values()) else "failed"


def quantile(values, probability):
    return sorted(values)[max(0, math.ceil(len(values) * probability) - 1)] if values else None


def summarize(rows):
    """只汇总同一批传入结果；运行方式仍分组，控制故障不混入在线延迟。"""
    executed = [row for row in rows if row["status"] != "skipped"]
    traces = [row["trace"] for row in executed if row.get("trace", {}).get("candidate_events_available")]
    positive = [trace for trace in traces if trace["required_groups"]]
    conditional = [trace for trace in positive if trace["candidate_complete"]]
    all_traces = [row["trace"] for row in executed if row.get("trace", {}).get("trace_available")]
    latencies = [row["elapsed_ms"] for row in executed if row.get("elapsed_ms") is not None]
    return {
        "scheduled": len(rows), "executed": len(executed), "statuses": dict(Counter(row["status"] for row in rows)),
        "task_success": ratio(sum(row["status"] == "passed" for row in executed), len(executed)),
        "partial_success": ratio(sum(row["status"] == "partial" for row in executed), len(executed)),
        "candidate_tool_group_recall": ratio(sum(trace["candidate_group_hits"] for trace in positive), sum(trace["required_groups"] for trace in positive)),
        "candidate_task_full_coverage": ratio(sum(trace["candidate_complete"] for trace in positive), len(positive)),
        "planner_tool_group_coverage_given_candidates": ratio(sum(trace["planner_group_hits"] for trace in conditional), sum(trace["required_groups"] for trace in conditional)),
        "parameter_schema_validity": ratio(sum(trace["schema_passed"] for trace in all_traces), sum(trace["schema_total"] for trace in all_traces)),
        "quality_pass": ratio(sum(trace["quality_valid"] for trace in all_traces), sum(trace["quality_total"] for trace in all_traces)),
        "call_statuses": dict(sum((Counter(trace["call_statuses"]) for trace in all_traces), Counter())),
        "missing_candidate_trace_cases": sum(row.get("runner") == "chat" and not row.get("trace", {}).get("candidate_events_available") for row in executed),
        "forbidden_candidates": sum(len(trace["forbidden_candidate_keys"]) for trace in traces),
        "p50_ms": quantile(latencies, .5), "p95_ms": quantile(latencies, .95),
        "sample_size": len(latencies),
        "final_answer_token_usage_cases": sum(row.get("final_answer_input_tokens") is not None for row in executed),
        "complete_task_token_cost": None,
        "planner_dependency_correctness": None,
        "answer_semantic_faithfulness": None,
        "recovery_success": None,
    }


def report(dataset, rows):
    return {
        "dataset": dataset["name"], "dataset_version": dataset["version"],
        "target_quotas": dataset["target_quotas"], "target_total": sum(dataset["target_quotas"].values()),
        "available_cases": len(dataset["cases"]),
        "annotation": dataset["annotation"],
        "measurement_limits": [
            "每个Tool组为OR，组间为AND；候选和规划为本次任务各轮集合，不是逐轮金标准。",
            "条件Planner指标仅指必需工具组覆盖，不代表参数/依赖/额外调用全部正确。",
            "Schema合法不等于业务参数正确；事实词组与引用标签合法不等于语义忠实。",
            "未测的恢复率/依赖正确率/完整任务Token成本为null，未运行和不支持不能记通过。",
            "小样本经验P95不是生产SLA；Chat、后台文档与受控故障必须分别比较。",
        ],
        "summary": summarize(rows),
        "by_category": {key: summarize([row for row in rows if row["category"] == key]) for key in dataset["target_quotas"]},
        "by_runner": {key: summarize([row for row in rows if row["runner"] == key]) for key in sorted({row["runner"] for row in rows})},
        "cases": rows,
    }


def render_html(payload):
    """离线只读视图；所有题目、模型文本和错误字段必须转义。"""
    body = []
    for row in payload["cases"]:
        text = json.dumps({"checks": row.get("checks"), "trace": row.get("trace"), "answer": row.get("answer"), "error_type": row.get("error_type")}, ensure_ascii=False, indent=2)
        body.append("<tr>" + "".join(f"<td>{html.escape(str(row.get(key, '')))}</td>" for key in ("id", "category", "business", "runner", "status", "elapsed_ms"))
                    + "<td><details><summary>结果与Trace</summary><pre>" + html.escape(text) + "</pre></details></td></tr>")
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Agent业务评测</title><style>body{font:15px system-ui;max-width:1400px;margin:30px auto;padding:16px}table{border-collapse:collapse;width:100%}td,th{border:1px solid #ddd;padding:10px;text-align:left;vertical-align:top}pre{white-space:pre-wrap;max-width:720px}summary{cursor:pointer}</style><h1>Agent业务评测</h1><p>' \
        + html.escape(payload["annotation"]) + '</p><p>目标200；现有题目' + str(payload["available_cases"]) + '；本次执行' + str(payload["summary"]["executed"]) + '</p><pre>' \
        + html.escape(json.dumps(payload["summary"], ensure_ascii=False, indent=2)) + '</pre><table><thead><tr><th>任务</th><th>类别</th><th>业务</th><th>执行链</th><th>结果</th><th>毫秒</th><th>检查明细</th></tr></thead><tbody>' + ''.join(body) + '</tbody></table></html>'
