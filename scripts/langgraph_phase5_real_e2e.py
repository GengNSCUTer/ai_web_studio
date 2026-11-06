"""复用真实文档验收，额外核对原生阶段/审批检查点；报告不覆盖历史批次。"""

import argparse
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))


def run(output):
    # 仅真正执行浏览器时加载Playwright依赖，CI纯分析/模块导入不依赖浏览器环境。
    import document_task_real_acceptance as acceptance
    from app.core.database import SessionLocal
    from app.models.agent_runtime import AgentRun
    from sqlalchemy import func, select, text

    report = {"scope": "在线模型、真实文件、浏览器/Word下载、独立阶段进程、托管Worker、PG原生图"}
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(AgentRun).where(AgentRun.status.in_(["queued", "running"]))) == 0, \
            "已有其他后台任务，不暂停其Worker"
    original_cleanup = acceptance.cleanup
    native = {}

    def audit_cleanup(pid, cid):
        try:
            with SessionLocal() as db:
                runs = list(db.scalars(select(AgentRun).where(AgentRun.project_id == pid)))
                native["run_engines"] = [json.loads(run.planner_state_json).get("runtime_engine") for run in runs]
                rows = db.execute(text("SELECT t.runtime_key,count(*) FROM aiws_graph.runtime_threads t "
                    "JOIN agent_runs r ON r.id=t.run_id WHERE r.project_id=:pid GROUP BY t.runtime_key"), {"pid": pid}).all()
                native["threads_by_runtime"] = {key: count for key, count in rows}
                values = db.execute(text("SELECT c.checkpoint FROM aiws_graph.checkpoints c "
                    "JOIN aiws_graph.runtime_threads t ON t.thread_id=c.thread_id JOIN agent_runs r ON r.id=t.run_id "
                    "WHERE r.project_id=:pid"), {"pid": pid}).scalars().all()
                native["checkpoint_count"] = len(values)
                native["checkpoint_excludes_document_body"] = all("data_base64" not in str(value) and "文档交付蓝鲸" not in str(value)
                    for value in values)
        finally:
            original_cleanup(pid, cid)

    acceptance.cleanup = audit_cleanup
    try:
        captured = io.StringIO()
        with redirect_stdout(captured):
            acceptance.run()
        report["business"] = json.loads(captured.getvalue().strip().splitlines()[-1])
        report["native"] = native
        assert native["run_engines"] and set(native["run_engines"]) == {"langgraph_document_v1"}
        assert native["threads_by_runtime"].get("langgraph_document_v1", 0) >= 7
        assert native["threads_by_runtime"].get("langgraph_document_approval_v1", 0) == 1
        assert native["checkpoint_excludes_document_body"]
        report["passed"] = True
    except Exception as exc:
        report.update(passed=False, error_type=type(exc).__name__, native=native)
        raise
    finally:
        acceptance.cleanup = original_cleanup
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.output), ensure_ascii=False, indent=2))
