"""用真实在线 Chat 比较四种同步 Tool 模式的预算表现。

脚本只创建临时项目和一份脱敏 Markdown 文件，使用固定测试账号已有的在线
Provider，不修改生产预算常量、不启动本地模型，也不批准文件写入。结果同时
保存请求总耗时、工具阶段耗时、轮次、调用数、终止原因和最终消息状态。
"""

from __future__ import annotations

import argparse
import json
import math
import re
import statistics
import sys
import time
import uuid
from pathlib import Path

import httpx
from sqlalchemy import delete, select

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.database import SessionLocal  # noqa: E402
from app.models.conversation import Conversation  # noqa: E402
from app.models.message import Message  # noqa: E402
from app.models.observability import ChatRuntimeMetric  # noqa: E402
from app.models.project import Project  # noqa: E402
from app.models.project_file import ProjectFile  # noqa: E402
from app.models.tool_trace import ToolCallRun, ToolRouteRun  # noqa: E402
from app.models.user import User  # noqa: E402
from app.repositories.user_repo import UserRepository  # noqa: E402
from app.services.auth_service import AuthService  # noqa: E402


MODES = ("quick_chat", "guided_research", "workspace_review", "edit_proposal")
CASES = {
    "quick_chat": [
        ("file_read", "请读取 evaluation-notes.md，告诉我项目代号和保留天数，不要联网。"),
        ("weather", "只用高德查询深圳当前天气和温度，不要网页搜索。"),
        ("multi_read", "读取 evaluation-notes.md 并查询深圳和北京当前天气，分别报告项目代号、保留天数和两地气温。只用文件工具和高德，不要网页搜索。"),
    ],
    "guided_research": [
        ("web_search", "使用 Tavily 搜索 PostgreSQL 官方文档的 WAL 机制，给出简短说明和来源链接。"),
        ("documentation", "使用 Context7 查询 FastAPI StreamingResponse 的用途和基本用法，给出来源。"),
        ("research_multi", "使用 Context7 核实 httpx.Timeout 的四个超时维度，同时用 Tavily 查 FastAPI 官方 StreamingResponse，分别给出用途、简短代码和来源链接。不要访问工作区。"),
    ],
    "workspace_review": [
        ("file_read", "读取 evaluation-notes.md，报告项目代号、保留天数和用途，不要联网。"),
        ("multi_read", "读取 evaluation-notes.md 并查询深圳当前天气，分别报告资料和天气，不要网页搜索。"),
        ("file_compare", "从当前工作区找出项目备份与运行说明，读取两份文件原文，对比它们的保留周期和执行时间。请给出项目代号、保留天数及两份文件分别记录的时间，不要联网。"),
    ],
    "edit_proposal": [
        ("edit_proposal", "请读取 evaluation-notes.md，并提出把标题改为‘评测笔记’的修改提案；只生成提案，不要实际写入。"),
        ("edit_proposal", "请为 evaluation-notes.md 生成一个把标题改为‘评测笔记’的 Diff 草稿，保留原文并等待用户确认，不要实际修改文件。"),
        ("edit_locate", "找到工作区内 CedarLedger 的备份说明，读取原文后提出把保留周期从 17 days 改为 21 days 的 Diff 草稿。只生成预览，不要写入或修改其他文件。"),
    ],
}


def percentile(values: list[float], probability: float) -> float | None:
    """使用 nearest-rank 计算样本分位数；样本太少时报告样本最大值语义。"""

    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(probability * len(ordered)) - 1))
    return round(ordered[index], 1)


def fixture() -> tuple[str, str, str, str]:
    """创建临时项目、文件和访问令牌。"""

    with SessionLocal() as db:
        user = db.scalar(select(User).where(User.email == "1528713326@qq.com"))
        if user is None:
            raise RuntimeError("固定测试账号不存在。")
        project = Project(
            user_id=user.id,
            name=f"budget-eval-{uuid.uuid4().hex[:10]}",
            default_model="deepseek-ai/DeepSeek-V3.2",
        )
        db.add(project)
        db.flush()
        file_row = ProjectFile(
            user_id=user.id,
            project_id=project.id,
            kind="text",
            mime_type="text/markdown",
            file_name="evaluation-notes.md",
            file_size=180,
            storage_key=f"budget-eval/{project.id}/evaluation-notes.md",
            parsed_text=(
                "# Evaluation note\n"
                "Project code: CedarLedger.\n"
                "Backup retention: 17 days.\n"
                "Purpose: validate the bounded tool workflow.\n"
            ),
        )
        db.add(file_row)
        db.add(ProjectFile(
            user_id=user.id, project_id=project.id, kind="text", mime_type="text/markdown",
            file_name="operations.md", file_size=120,
            storage_key=f"budget-eval/{project.id}/operations.md",
            parsed_text="# 运行说明\nProject code: CedarLedger.\nBackup retention: 17 days.\nExecution time: 03:10 UTC.\n",
        ))
        file_row.parsed_text += "Execution time: 02:30 UTC.\n"
        db.commit()
        token = AuthService(UserRepository(db)).create_access_token(user)
        return token, user.id, project.id, file_row.id


def cleanup(user_id: str, project_id: str) -> None:
    """删除本轮临时会话、工具轨迹和项目。"""

    with SessionLocal() as db:
        conversations = list(db.scalars(select(Conversation).where(Conversation.user_id == user_id, Conversation.project_id == project_id)))
        conversation_ids = [item.id for item in conversations]
        if conversation_ids:
            routes = list(db.scalars(select(ToolRouteRun.id).where(ToolRouteRun.conversation_id.in_(conversation_ids))))
            if routes:
                db.execute(delete(ToolCallRun).where(ToolCallRun.route_run_id.in_(routes)))
                db.execute(delete(ToolRouteRun).where(ToolRouteRun.id.in_(routes)))
            for item in conversations:
                db.delete(item)
        project = db.get(Project, project_id)
        if project:
            db.delete(project)
        db.commit()


def evaluation_sources(calls: list) -> list[dict]:
    """预览成功会阻断写入依赖并记waiting_approval；该状态不代表Diff生成失败。"""

    return [source for item in calls
        if item.status == "success" or (item.tool_key == "workspace.files.propose_edit" and item.status == "waiting_approval")
        for source in json.loads(item.sources_json or "[]")]


def check_task(name: str, query: str, answer: str, sources: list[dict], calls: list[dict], unchanged: bool) -> dict[str, bool]:
    """只检查预先规定的业务断言，不把 done 或 HTTP 200 当成任务完成。"""

    checks: dict[str, bool] = {}
    # Markdown 数值可能被反引号或加粗包裹，不能误判为缺少事实。
    plain_answer = re.sub(r"[`*]", "", answer)
    successful = {item["tool_key"] for item in calls if item["status"] == "success"}
    reads = [item for item in sources if item.get("source_type") == "workspace_file_read"]
    if name in {"file_read", "multi_read", "file_compare"}:
        checks["file_body_read"] = bool(reads)
        checks["project_fact"] = "CedarLedger" in answer
        checks["retention_fact"] = bool(re.search(r"(?<!\d)17(?!\d)", answer))
    if name == "file_compare":
        checks["two_files_read"] = len({item.get("metadata", {}).get("file_id") for item in reads}) >= 2
        checks["both_times"] = "02:30" in answer and "03:10" in answer
    if name in {"weather", "multi_read"}:
        for city in ("深圳", "北京"):
            if city not in query:
                continue
            weather = [item.get("metadata", {}).get("raw", {}) for item in sources if item.get("source_type") == "weather"]
            checks[f"live_weather_{city}"] = any(
                city in str(item.get("city", "")) and item.get("weather_data_type") == "live"
                and re.fullmatch(r"-?\d+(?:\.\d+)?", str(item.get("temperature", ""))) is not None
                and re.search(r"(?<!\d)" + re.escape(str(item["temperature"])) + r"\s*(?:°\s*C|℃|摄氏度|度)", plain_answer, re.I) is not None
                for item in weather
            )
    if name in {"web_search", "research_multi"}:
        checks["requested_search_executed"] = "web.tavily.search" in successful
        domain = "postgresql.org" if name == "web_search" else "fastapi.tiangolo.com"
        checks["official_url_used"] = any(domain in (item.get("url") or "") and item.get("url") in answer for item in sources)
    if name in {"documentation", "research_multi"}:
        checks["documentation_body"] = "docs.context7.query" in successful and any(item.get("source_type") == "documentation" for item in sources)
        checks["doc_terms"] = ("StreamingResponse" in answer if name == "documentation" else all(term in answer.lower() for term in ("connect", "read", "write", "pool")))
    if name in {"edit_proposal", "edit_locate"}:
        previews = [item for item in sources if item.get("source_type") == "workspace_file_edit_preview"
                    and item.get("metadata", {}).get("raw", {}).get("applied") is False]
        preview_call_completed = any(item["tool_key"] == "workspace.files.propose_edit" and item["status"] in {"success", "waiting_approval"} for item in calls)
        checks["real_diff_preview"] = preview_call_completed and bool(previews)
        checks["requested_replacement"] = any(("21" if name == "edit_locate" else "评测笔记") in item.get("display_text", "") for item in previews)
        checks["file_unchanged"] = unchanged
        checks["not_claimed_written"] = bool(re.search(r"未|草稿|预览|提案|确认", answer))
    return checks


def run_case(client: httpx.Client, *, project_id: str, mode: str, name: str, query: str, index: int) -> dict:
    """执行一条真实 Chat，并从持久化 ToolRouteRun 读取预算诊断。"""

    started = time.perf_counter()
    response = client.post(
        "/api/conversations",
        json={"title": f"budget-{mode}-{index}", "project_id": project_id, "model_name": "deepseek-ai/DeepSeek-V3.2"},
    )
    response.raise_for_status()
    conversation_id = response.json()["id"]
    events = []
    first_answer_ms = None
    with client.stream(
        "POST",
        "/api/chat/events-stream",
        json={
            "conversation_id": conversation_id,
            "content": query,
            "model_name": "deepseek-ai/DeepSeek-V3.2",
            "web_search_enabled": True,
            "tool_run_mode": mode,
        },
    ) as stream:
        stream.raise_for_status()
        for line in stream.iter_lines():
            if not line.strip():
                continue
            event = json.loads(line)
            events.append(event)
            if event.get("type") == "answer_delta" and first_answer_ms is None:
                first_answer_ms = round((time.perf_counter() - started) * 1000, 1)
    wall_ms = round((time.perf_counter() - started) * 1000, 1)

    with SessionLocal() as db:
        route = db.scalar(select(ToolRouteRun).where(ToolRouteRun.conversation_id == conversation_id).order_by(ToolRouteRun.created_at.desc()))
        if route is None:
            raise RuntimeError("未找到工具轨迹。")
        answer = db.get(Message, route.assistant_message_id)
        calls = list(db.scalars(select(ToolCallRun).where(ToolCallRun.route_run_id == route.id)))
        trace = json.loads(route.events_json or "[]")
        metric = db.scalar(select(ChatRuntimeMetric).where(ChatRuntimeMetric.assistant_message_id == route.assistant_message_id))
        stats = json.loads(metric.stats_json or "{}") if metric else {}
        budget = stats.get("external_tool_run_budget") or next((event["budget"] for event in reversed(trace) if event.get("budget")), {})
        if not budget or "planning_rounds_used" not in budget:
            raise RuntimeError("预算采集缺失，不可用于调参。")
        reason = stats.get("external_agent_terminal_reason") or next((event.get("reason") for event in reversed(trace) if event.get("type") == "tool_agent_terminal"), None)
        call_rows = [{"tool_key": item.tool_key, "status": item.status, "elapsed_ms": item.elapsed_ms} for item in calls]
        sources = evaluation_sources(calls)
        files = list(db.scalars(select(ProjectFile).where(ProjectFile.project_id == project_id)))
        unchanged = all("17 days" in item.parsed_text and "评测笔记" not in item.parsed_text for item in files)
        checks = check_task(name, query, answer.content or "", sources, call_rows, unchanged) if answer else {}
        stream_done = any(item.get("type") == "done" for item in events)
        passed = bool(answer and answer.status == "done" and stream_done and checks and all(checks.values()))
        task_status = "complete" if passed else "partial" if any(checks.values()) else "failed"
        return {
            "mode": mode,
            "case": name,
            "query": query,
            "conversation_id": conversation_id,
            "assistant_status": answer.status if answer else "missing",
            "route_status": route.status,
            "full_answer": bool(answer and answer.status == "done"),
            "task_status": task_status,
            "checks": checks,
            "answer": answer.content if answer else "",
            "tool_calls": len(calls),
            "successful_tool_calls": sum(item.status == "success" for item in calls),
            "planning_rounds": budget.get("planning_rounds_used"),
            "tool_calls_used": budget.get("tool_calls_used"),
            "tool_elapsed_ms": budget.get("elapsed_ms"),
            "budget_reason": reason,
            "tool_wall_clock_limit_s": budget.get("max_wall_clock_seconds"),
            "planning_elapsed_ms": stats.get("external_planning_latency_ms"),
            "execution_elapsed_ms": stats.get("external_execution_latency_ms"),
            "first_answer_ms": first_answer_ms,
            "final_answer_input_tokens": stats.get("provider_input_tokens"),
            "final_answer_output_tokens": stats.get("provider_output_tokens"),
            "planner_token_usage": None,
            "calls_detail": call_rows,
            "candidate_keys": list(dict.fromkeys(item["tool_key"] for event in trace if event.get("type") == "tool_candidate_selection" for item in event.get("candidates", []))),
            "planned_keys": list(dict.fromkeys(item["tool_key"] for event in trace if event.get("type") == "tool_plan" for item in event.get("calls", []))),
            "preview_planned_arguments": [item.get("arguments") for event in trace if event.get("type") == "tool_plan" for item in event.get("calls", []) if item.get("tool_key") == "workspace.files.propose_edit"],
            "call_error_events": [event for event in trace if event.get("type") in {"tool_call_error", "tool_schema_validation"} and event.get("status") != "passed"],
            "evidence_sufficiency_status": stats.get("evidence_sufficiency_status"),
            "sources_for_checks": sources,
            "fallback_reasons": [item.get("reason") for item in trace if item.get("type") == "tool_fallback"],
            "budget": budget,
            "terminal_events": [item for item in trace if item.get("type") in {"tool_agent_terminal", "tool_agent_budget_timeout", "tool_agent_round_end"}],
            "tool_events": len(trace),
            "total_wall_clock_ms": wall_ms,
            "stream_done": stream_done,
            "answer_chars": len(answer.content or "") if answer else 0,
        }


def snapshot_checks(input_path: Path, output_path: Path) -> None:
    """在临时数据清理前留存业务证据；也用于按相同原答案修正评分器问题。"""

    payload = json.loads(input_path.read_text())
    rows = payload["rows"]
    with SessionLocal() as db:
        for row in rows:
            cid = row.get("conversation_id")
            if not cid:
                continue
            route = db.scalar(select(ToolRouteRun).where(ToolRouteRun.conversation_id == cid))
            if not route:
                continue
            calls = list(db.scalars(select(ToolCallRun).where(ToolCallRun.route_run_id == route.id)))
            row["query"] = route.query
            row["sources_for_checks"] = evaluation_sources(calls)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def rescore(input_path: Path, evidence_path: Path, output_path: Path) -> None:
    """复用原请求和原答案修正Markdown评分误差，不覆盖原始实验文件。"""

    payload = json.loads(input_path.read_text())
    evidence = {row.get("conversation_id"): row for row in json.loads(evidence_path.read_text())["rows"]}
    changed = []
    for row in payload["rows"]:
        if "error_type" in row:
            continue
        saved = evidence.get(row.get("conversation_id"), {})
        if "sources_for_checks" not in saved:
            continue
        before = row["checks"]
        row["query"] = saved["query"]
        row["sources_for_checks"] = saved["sources_for_checks"]
        row["checks"] = check_task(row["case"], row["query"], row["answer"], row["sources_for_checks"], row["calls_detail"], before.get("file_unchanged", True))
        row["task_status"] = "complete" if row["full_answer"] and row["stream_done"] and all(row["checks"].values()) else "partial" if any(row["checks"].values()) else "failed"
        if before != row["checks"]:
            changed.append(row["conversation_id"])
    payload["metrics"] = summarize(payload["rows"])
    payload["rescoring"] = {"reason": "按留存的同一工具证据和答案使用当前断言重算：温度接受Markdown；只读Diff可在waiting_approval状态展示，但必须有applied=false的真实预览来源。原始分数保留。", "changed_cases": changed}
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def serve_variant(edit_wall_seconds: int) -> None:
    """只在独立环回测试进程调整编辑时长，生产服务及其他参数不受影响。"""

    from dataclasses import replace
    from unittest.mock import patch
    import uvicorn
    from app.main import app
    from app.services.external_context_service import ExternalContextService

    def factory(**kwargs):
        service = ExternalContextService(**kwargs)
        if service.run_policy.mode == "edit_proposal":
            service.run_policy = replace(service.run_policy, max_wall_clock_seconds=edit_wall_seconds)
        return service

    with patch("app.services.chat_context_assembly_service.ExternalContextService", side_effect=factory):
        uvicorn.run(app, host="127.0.0.1", port=32107, log_level="warning")


def summarize(rows: list[dict]) -> dict:
    """按模式输出分布；小样本的 p99 明确等同于样本最大值。"""

    output = {}
    for mode in MODES:
        group = [row for row in rows if row["mode"] == mode]
        totals = [row["total_wall_clock_ms"] for row in group if row.get("total_wall_clock_ms") is not None]
        tools = [row["tool_elapsed_ms"] for row in group if row.get("tool_elapsed_ms") is not None]
        output[mode] = {
            "samples": len(group),
            "request_errors": sum("error_type" in row for row in group),
            "full_answer_rate": round(sum(row.get("full_answer", False) for row in group) / len(group), 3) if group else None,
            "task_statuses": {status: sum(row.get("task_status", "failed") == status for row in group) for status in ("complete", "partial", "failed")},
            "tool_success_rate": round(sum(row.get("successful_tool_calls", 0) for row in group) / sum(row.get("tool_calls", 0) for row in group), 3) if sum(row.get("tool_calls", 0) for row in group) else None,
            "mean_total_ms": round(statistics.mean(totals), 1) if totals else None,
            "p50_total_ms": percentile(totals, 0.50),
            "p95_total_ms": percentile(totals, 0.95),
            "p99_total_ms_sample_max": percentile(totals, 0.99),
            "mean_tool_ms": round(statistics.mean(tools), 1) if tools else None,
            "p95_tool_ms": percentile(tools, 0.95),
            "rounds": [row.get("planning_rounds") for row in group],
            "calls": [row.get("tool_calls_used") for row in group],
            "terminal_reasons": {str(reason): sum(row.get("budget_reason") == reason for row in group) for reason in sorted({row.get("budget_reason") for row in group}, key=str)},
            "final_answer_tokens_per_complete_task": round(sum((row.get("final_answer_input_tokens") or 0) + (row.get("final_answer_output_tokens") or 0) for row in group if row.get("task_status") == "complete") / sum(row.get("task_status") == "complete" for row in group), 1) if any(row.get("task_status") == "complete" for row in group) and all(row.get("final_answer_input_tokens") is not None and row.get("final_answer_output_tokens") is not None for row in group if row.get("task_status") == "complete") else None,
        }
    return output


def main(output: Path, backend: str, repetitions: int, modes: list[str]) -> None:
    token, user_id, project_id, _ = fixture()
    rows: list[dict] = []
    try:
        with httpx.Client(base_url=backend, headers={"Authorization": f"Bearer {token}"}, timeout=300) as client:
            index = 0
            for _ in range(repetitions):
                for mode in modes:
                    for name, query in CASES[mode]:
                        index += 1
                        case_started = time.perf_counter()
                        try:
                            row = run_case(client, project_id=project_id, mode=mode, name=name, query=query, index=index)
                        except Exception as exc:  # 保留单条失败，继续完成分布采样
                            # Provider 原始异常可能含端点或凭据，只保存类型。
                            row = {"mode": mode, "case": name, "error_type": type(exc).__name__, "total_wall_clock_ms": round((time.perf_counter() - case_started) * 1000, 1)}
                        rows.append(row)
                        output.parent.mkdir(parents=True, exist_ok=True)
                        output.write_text(json.dumps({"state": "running", "rows": rows}, ensure_ascii=False, indent=2) + "\n")
                        print(json.dumps({key: row.get(key) for key in ("mode", "case", "task_status", "planning_rounds", "tool_calls_used", "tool_elapsed_ms", "budget_reason", "total_wall_clock_ms", "error_type")}, ensure_ascii=False), flush=True)
        payload = {
            "model": "deepseek-ai/DeepSeek-V3.2",
            "repetitions": repetitions,
            "sample_limit": "每个模式3种固定任务，默认重复2次；p95/p99仅为小样本经验分位数，不是SLA或通用成功率。",
            "cost_limit": "仅记录最终回答Provider返回的输入/输出token；Planner complete_chat不暴露usage，Embedding/工具计费未取账单，不能据此算完整任务金额。",
            "metrics": summarize(rows),
            "rows": rows,
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(payload["metrics"], ensure_ascii=False), flush=True)
    finally:
        cleanup(user_id, project_id)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--backend", default="http://127.0.0.1:32007")
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument("--modes", choices=MODES, nargs="+", default=list(MODES))
    parser.add_argument("--snapshot-from", type=Path)
    parser.add_argument("--rescore-from", type=Path)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--serve-edit-wall-seconds", type=int, choices=(35, 50))
    args = parser.parse_args()
    if args.serve_edit_wall_seconds:
        serve_variant(args.serve_edit_wall_seconds)
    elif not args.output:
        parser.error("评测和快照需要 --output。")
    elif args.snapshot_from:
        snapshot_checks(args.snapshot_from, args.output)
    elif args.rescore_from:
        if not args.evidence:
            parser.error("复评分需要 --evidence。")
        rescore(args.rescore_from, args.evidence, args.output)
    else:
        main(args.output, args.backend, max(1, args.repetitions), args.modes)
