"""Agent评测离线分析：层次和分母显式，不以HTTP或done替代业务成功。"""

from collections import Counter
import html
import io
import json
import math
import re
import zipfile
from xml.etree import ElementTree

from app.services.citation_audit_service import citation_labels


def ratio(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator,
            "value": numerator / denominator if denominator else None}


def facts_present(answer, groups):
    """等价词组逐组判定，仅用于预设有限事实，不等于整篇答案语义正确。"""
    plain = re.sub(r"[`*]", "", answer).casefold()
    return [any(re.search(r"(?<!\d)" + re.escape(term) + r"(?!\d)", plain) if term.isdigit()
                else term.casefold() in plain for term in group) for group in groups]


def check_answer(answer, expected):
    """预先标注的有限事实；不拿关键词核对冒充整篇语义评审。"""
    body = re.sub(r'\[(?:KB|T)\d+\]', '', answer)
    checks = {f"fact_{index + 1}": hit for index, hit in enumerate(facts_present(body, expected.get('facts', [])))}
    checks['forbidden_claims_absent'] = not any(term.casefold() in answer.casefold()
                                               for term in expected.get('forbidden_answer_terms', []))
    return checks


def check_chat_evidence(answer, sources, expected, file_ids, gold_chunk_ids=()):
    """只认可最终注入来源；原始召回或合法编号不能证明语义支持性。"""
    final = [source for source in sources if source.get('used_in_prompt') is True
             and (source.get('metadata') or {}).get('citation_scope') == 'final_prompt'
             and (source.get('metadata') or {}).get('citation_body_chars', 0) > 0]
    labels = set(citation_labels(answer))
    available = {source.get('citation_label') for source in final}
    checks = {}
    if expected.get('citation_required'):
        checks['citations_present_and_legal'] = bool(labels) and labels.issubset(available)
    read_ids = {source.get('metadata', {}).get('file_id') for source in final
                if source.get('source_type') == 'workspace_file_read' and source.get('citation_label') in labels}
    for name in expected.get('read_files', []):
        checks['cited_file:' + name] = file_ids.get(name) in read_ids
    if expected.get('knowledge_required'):
        evidence_ids = set()
        knowledge = [source for source in final if source.get('source_type') == 'knowledge'
                     and source.get('citation_label') in labels]
        for source in knowledge:
            metadata = source.get('metadata') or {}
            evidence_ids.add(metadata.get('chunk_id'))
            evidence_ids.update(metadata.get('matched_child_ids') or [])
        checks['knowledge_cited'] = bool(knowledge)
        checks['gold_chunk_cited'] = bool(evidence_ids.intersection(gold_chunk_ids))
    return checks


def inspect_download(data, file_format):
    """检查真实下载格式和正文，来源附录不参与业务事实得分。"""
    if not data or len(data) > 1_000_000:
        raise ValueError('导出文件为空或超过产品限额')
    if file_format == 'markdown':
        text = data.decode('utf-8', errors='strict')
        body = text.rsplit('\n## 来源\n', 1)[0]
    elif file_format == 'docx':
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            if sum(info.file_size for info in package.infolist()) > 10_000_000:
                raise ValueError('Word展开后超过验收限额')
            if '[Content_Types].xml' not in package.namelist():
                raise ValueError('不是有效Word文件')
            root = ElementTree.fromstring(package.read('word/document.xml'))
        ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        paragraphs = [''.join(node.text or '' for node in paragraph.findall('.//w:t', ns))
                      for paragraph in root.findall('.//w:p', ns)]
        text = '\n'.join(paragraphs)
        body = text.rsplit('\n来源\n', 1)[0]
    else:
        raise ValueError('不支持的产物格式')
    if not body.strip():
        raise ValueError('产物正文为空')
    return {'text': text, 'body': body, 'bytes': len(data), 'format_valid': True}


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
    positive = [trace for trace in traces if trace["required_groups"] and trace.get('capability_metrics_applicable',True)]
    conditional = [trace for trace in positive if trace["candidate_complete"]]
    all_traces = [row["trace"] for row in executed if row.get("trace", {}).get("trace_available")]
    latencies = [row["elapsed_ms"] for row in executed if row.get("elapsed_ms") is not None]
    return {
        "scheduled": len(rows), "executed": len(executed), "statuses": dict(Counter(row["status"] for row in rows)),
        "task_success": ratio(sum(row["status"] == "passed" for row in executed), len(executed)),
        "scheduled_task_success": ratio(sum(row["status"] == "passed" for row in executed), len(rows)),
        "partial_success": ratio(sum(row["status"] == "partial" for row in executed), len(executed)),
        "candidate_tool_group_recall": ratio(sum(trace["candidate_group_hits"] for trace in positive), sum(trace["required_groups"] for trace in positive)),
        "candidate_task_full_coverage": ratio(sum(trace["candidate_complete"] for trace in positive), len(positive)),
        "planner_tool_group_coverage_given_candidates": ratio(sum(trace["planner_group_hits"] for trace in conditional), sum(trace["required_groups"] for trace in conditional)),
        "parameter_schema_validity": ratio(sum(trace["schema_passed"] for trace in all_traces), sum(trace["schema_total"] for trace in all_traces)),
        "quality_pass": ratio(sum(trace["quality_valid"] for trace in all_traces), sum(trace["quality_total"] for trace in all_traces)),
        "call_statuses": dict(sum((Counter(trace["call_statuses"]) for trace in all_traces), Counter())),
        "call_execution_success": ratio(sum(trace['call_statuses'].get('success', 0) for trace in all_traces),
                                        sum(sum(trace['call_statuses'].values()) for trace in all_traces)),
        "no_tool_task_success": ratio(sum(row['status'] == 'passed' for row in executed if row['category'] == 'no_tool'),
                                      sum(row['category'] == 'no_tool' for row in executed)),
        "artifact_download_success": ratio(sum(row.get('checks', {}).get('download_valid') is True for row in executed if row['runner'] == 'document_task'),
                                           sum(row['runner'] == 'document_task' for row in executed)),
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
            "Schema/质量为Trace事件计数；调用成功分母含skipped审计行，不冒充实际网络请求成功率。",
            "本批为已启用用户记忆和既有工具配置的在线行为，非隔离关闭记忆的对照实验。",
            "只读/歧义/原文不存在的编辑负例不进入正向能力召回分母，但仍进入任务成功率。未标注禁止候选的0计数不代表已证明越权泄漏率为0。",
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
        text = json.dumps({key: row.get(key) for key in ('checks', 'trace', 'answer', 'error_type', 'skip_reason',
                         'task_snapshot', 'downloads', 'result_messages')}, ensure_ascii=False, indent=2)
        links = ''.join('<p><a href="' + html.escape(item['path'], quote=True) + '">打开下载产物</a></p>'
                        for item in row.get('downloads', [])
                        if re.fullmatch(r'artifacts/[A-Za-z0-9_-]+\.(?:md|docx)', item.get('path', '')))
        body.append("<tr>" + "".join(f"<td>{html.escape(str(row.get(key, '')))}</td>" for key in ("id", "category", "business", "runner", "status", "elapsed_ms"))
                    + "<td><details><summary>结果与Trace</summary><pre>" + html.escape(text) + "</pre></details>" + links + "</td></tr>")
    groups = ''.join('<tr>' + ''.join('<td>' + html.escape(str(value)) + '</td>' for value in
                      (name, stats['executed'], stats['statuses'].get('passed', 0), stats['statuses'].get('partial', 0),
                       stats['statuses'].get('failed', 0), stats['p50_ms'], stats['p95_ms'])) + '</tr>'
                     for name, stats in payload['by_runner'].items())
    return '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src \'none\'; style-src \'unsafe-inline\'"><title>Agent业务评测</title><style>body{font:15px system-ui;max-width:1400px;margin:30px auto;padding:16px;color:#183042;background:#f7f9fc}table{border-collapse:collapse;width:100%;background:white;margin:20px 0}td,th{border:1px solid #dce2e8;padding:10px;text-align:left;vertical-align:top}th{background:#eef3f8}pre{white-space:pre-wrap;max-width:100%;overflow-wrap:anywhere}summary{cursor:pointer}</style><h1>Agent业务评测</h1><p>' \
        + html.escape(payload["annotation"]) + '</p><p>目标' + str(payload['target_total']) + '；现有题目' + str(payload["available_cases"]) + '；本次执行' + str(payload["summary"]["executed"]) \
        + '；结果 ' + html.escape(json.dumps(payload['summary']['statuses'], ensure_ascii=False)) \
        + '</p><table><thead><tr><th>执行链</th><th>运行数</th><th>通过</th><th>部分</th><th>失败</th><th>P50 ms</th><th>经验P95 ms</th></tr></thead><tbody>' + groups \
        + '</tbody></table><details><summary>展开分层指标、分母和适用边界</summary><pre>' \
        + html.escape(json.dumps({key: payload[key] for key in ('summary', 'by_category', 'by_runner', 'measurement_limits')}, ensure_ascii=False, indent=2)) + '</pre></details><table id="cases"><thead><tr><th>任务</th><th>类别</th><th>业务</th><th>执行链</th><th>结果</th><th>毫秒</th><th>检查明细</th></tr></thead><tbody>' + ''.join(body) + '</tbody></table></html>'
