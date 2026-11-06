"""复用已验证业务场景，额外核对持久化Trace确实经过LangGraph各节点。"""

import argparse
import asyncio
from contextlib import redirect_stdout
from importlib.metadata import version
import io
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def run_browser():
    import stage46a_real_e2e as smoke
    from sqlalchemy import select
    from app.core.database import SessionLocal
    from app.models.conversation import Conversation
    from app.models.tool_trace import ToolRouteRun

    original = smoke.collect_result
    def collect(**kwargs):
        result = original(**kwargs)
        with SessionLocal() as db:
            route = db.scalar(select(ToolRouteRun).join(Conversation,
                Conversation.id == ToolRouteRun.conversation_id).where(
                Conversation.project_id == kwargs["project_id"], ToolRouteRun.user_id == kwargs["user_id"]
            ).order_by(ToolRouteRun.created_at.desc()).limit(1))
            events = json.loads(route.events_json or "[]")
            nodes = [e["node"] for e in events if e.get("type") == "tool_graph_node"]
            assert {"plan", "execute", "observe", "finalize"}.issubset(nodes)
            assert nodes[-1] == "finalize"
            result["graph_nodes"] = nodes
        return result
    smoke.collect_result = collect
    try:
        with redirect_stdout(io.StringIO()) as captured:
            smoke.run()
        return json.loads(captured.getvalue())
    finally:
        smoke.collect_result = original


def run_faults():
    from stage46a_real_e2e import cleanup, prepare_fixture
    from tool_deadline_real_e2e import run_case, run_unavailable_planner_case
    _, uid, project_id, file_id, _ = prepare_fixture()
    try:
        return [asyncio.run(run_case(user_id=uid, project_id=project_id, first_file_id=file_id)),
                asyncio.run(run_unavailable_planner_case(user_id=uid, project_id=project_id))]
    finally:
        cleanup(user_id=uid, project_id=project_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=["browser", "faults", "all"], default="all")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = {"langgraph_version": version("langgraph"), "scope": "真实在线模型/工具/最终回答，故障由测试进程显式注入"}
    def save():
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.scenario in {"browser", "all"}:
        report["browser"] = run_browser()
        save()
    if args.scenario in {"faults", "all"}:
        report["faults"] = run_faults()
    save()
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
