"""隔离PG上的执行内核对照：真实文件工具/图/进程退出，固定Planner排除模型随机性。

正式Worker、在线模型、浏览器、审批与文档验收单独记录，不与受控实验混分母。
SQL仅计SQLAlchemy业务语句；原生Saver使用psycopg，其语句数不冒充已测。
"""

import argparse
import asyncio
from collections import Counter
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

ROOT = Path(os.environ.get("AIWS_BENCH_CODE_ROOT", Path(__file__).resolve().parents[1])).resolve()
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "backend"))


def context(harness, persistence, calls, *, more=False):
    from app.models.chat_run import ChatRun
    from app.services.chat_tool_runtime import current_tool_persistence
    from app.services.external_context_service import ExternalContextService
    from app.services.tools.run_policy import resolve_tool_run_policy
    from backend.tests.test_chat_tool_recovery import FixedPlanner

    async def execute():
        token = current_tool_persistence.set(persistence)
        try:
            with harness.sessions() as db:
                chat = db.get(ChatRun, persistence.claim.run_id)
                service = ExternalContextService(db=db, user_id="u", project_id="p", conversation_id="c",
                    assistant_message_id=chat.assistant_message_id, planner=FixedPlanner(calls, more=more),
                    run_policy=resolve_tool_run_policy("guided_research"))
                return await service.build_context(query="仅阅读工作区资料，不联网，汇总已核实内容", enabled=True, max_chars=4000)
        finally:
            current_tool_persistence.reset(token)
    return asyncio.run(execute())


def child(url, rid, claim, window, audit_path):
    from sqlalchemy import create_engine, event, text
    from sqlalchemy.orm import sessionmaker
    from app.services.chat_tool_runtime import ChatToolPersistence
    from app.services.tools.executor import ToolExecutor
    from backend.tests.test_chat_tool_recovery import ChatToolRecoveryTest

    engine = create_engine(url)
    harness = ChatToolRecoveryTest()
    harness.sessions = sessionmaker(engine, expire_on_commit=False, autoflush=False)
    counts = Counter()
    event.listen(engine, "before_cursor_execute", lambda *_: counts.update(sqlalchemy_statements=1))
    persistence = ChatToolPersistence(harness.sessions, claim)
    persistence.initialize()
    original_execute, original_complete, original_save = ToolExecutor._execute, persistence._complete_call, persistence.save

    def crash():
        Path(audit_path).write_text(json.dumps({"fault_epoch": time.time(), "counts": dict(counts)}))
        os._exit(84)

    if window == "unknown_write":
        with harness.sessions() as db:
            executor = ToolExecutor(db=db, user_id="u", project_id="p")
            call = harness.tool("write", "workspace.files.apply_edit")
            call.arguments.update(old_string="短事务", new_string="新内容")
            assert persistence._begin_call(executor, call)[0] == "execute"
        # 独立提交模拟外部副作用已发生但回执丢失，不调用真实未获批准的写工具。
        with engine.begin() as db:
            db.execute(text("INSERT INTO runtime_test_effects DEFAULT VALUES"))
        crash()

    async def execute(executor, call):
        if call.call_id == "A" and window == "before_call":
            crash()
        counts["tool_requests"] += 1
        result = await original_execute(executor, call)
        if call.call_id == "A" and window == "after_call":
            crash()
        return result

    def complete(step_id, result):
        original_complete(step_id, result)
        if window == "after_commit":
            crash()

    def save(run, node):
        if node == "observe" and window == "before_checkpoint":
            crash()
        original_save(run, node)

    persistence._complete_call, persistence.save = complete, save
    with patch.object(ToolExecutor, "_execute", execute):
        context(harness, persistence, [harness.tool("A"), harness.tool("B", "workspace.files.list", depends=["A"])])
    raise AssertionError("没有命中指定故障窗口")


def run(output, *, normal_repeats=0):
    from sqlalchemy import event, select, text
    from app.models.agent_runtime import AgentArtifact, AgentCheckpoint, AgentStep
    from app.models.chat_run import ChatRun
    from app.services.chat_tool_runtime import ChatToolPersistence
    from app.services.runtime_lease import aware, utcnow
    from app.services.tools.executor import ToolExecutor
    from backend.tests.test_chat_tool_recovery_postgres import ChatToolRecoveryPostgresTest

    if not os.environ.get("TEST_POSTGRES_URL"):
        raise RuntimeError("需要TEST_POSTGRES_URL，脚本只在自动创建的隔离PG库执行")
    report = {"scope":"隔离PG/真实文件工具/固定Planner/实际45秒租约/进程强退", "cases":[], "passed":False,
        "cost_scope":"SQLAlchemy业务SQL计数，不含原生Saver的psycopg语句；固定Planner不调用模型API",
        "code_commit":subprocess.check_output(["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True).strip()}
    if normal_repeats:
        report["scope"] = "隔离PG/真实文件工具/固定Planner/正常串行与并发；无进程故障"
    output.parent.mkdir(parents=True, exist_ok=True)
    def save():
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    ChatToolRecoveryPostgresTest.setUpClass()
    try:
        windows = (["normal", "parallel"] * normal_repeats if normal_repeats else
            ["normal", "parallel", "before_call", "after_call", "after_commit", "before_checkpoint", "unknown_write"])
        for sample, window in enumerate(windows, start=1):
            harness = ChatToolRecoveryPostgresTest()
            harness.setUp()
            counts = Counter()
            listener = lambda *_: counts.update(sqlalchemy_statements=1)
            case = {"case":window,"sample":sample,"passed":False}
            started = time.perf_counter()
            try:
                with harness.engine.begin() as db:
                    db.execute(text("CREATE TABLE IF NOT EXISTS runtime_test_effects(id bigserial PRIMARY KEY)"))
                    db.execute(text("TRUNCATE runtime_test_effects"))
                rid, claim, first = harness.fixture()
                event.listen(harness.engine, "before_cursor_execute", listener)
                before_artifacts, reused, wait_seconds = 0, 0, 0
                original_execute = ToolExecutor._execute

                async def execute(executor, call):
                    counts["tool_requests"] += 1
                    return await original_execute(executor, call)

                if window not in {"normal", "parallel"}:
                    audit = output.parent / ("." + output.stem + "-" + window + ".tmp.json")
                    process = multiprocessing.get_context("spawn").Process(target=child,
                        args=(harness.engine.url.render_as_string(hide_password=False), rid, claim, window, str(audit)))
                    process.start()
                    process.join(35)
                    if process.is_alive():
                        process.terminate()
                        process.join(5)
                        raise AssertionError("故障子进程未按期退出")
                    assert process.exitcode == 84, "未命中真实强退窗口"
                    prior = json.loads(audit.read_text())
                    audit.unlink()
                    counts.update(prior["counts"])
                    with harness.sessions() as db:
                        before_artifacts = len(list(db.scalars(select(AgentArtifact).where(AgentArtifact.run_id == first.agent_id))))
                        expires = aware(db.get(ChatRun, rid).lease_expires_at)
                    wait_started = time.perf_counter()
                    while utcnow() <= expires:
                        time.sleep(.25)
                    wait_seconds = time.perf_counter() - wait_started
                    next_claim = harness.call("claim_next", "recovery-worker")
                    assert next_claim and next_claim.run_id == rid
                    first = ChatToolPersistence(harness.sessions, next_claim)
                    first.initialize()
                    resumed_at = time.perf_counter()
                    if window == "unknown_write":
                        with harness.sessions() as db:
                            executor = ToolExecutor(db=db, user_id="u", project_id="p")
                            call = harness.tool("write", "workspace.files.apply_edit")
                            call.arguments.update(old_string="短事务", new_string="新内容")
                            assert first._begin_call(executor, call)[0] == "blocked"
                            call.call_id = "changed-id"
                            assert first._begin_call(executor, call)[0] == "blocked"
                            assert db.scalar(text("SELECT count(*) FROM runtime_test_effects")) == 1
                        case.update(business_outcome="blocked_pending_reconciliation", simulated_side_effect=True,
                            duplicate_side_effect_count=0, recovery_ready_ms=round((time.perf_counter()-resumed_at)*1000,2))
                    else:
                        with patch.object(ToolExecutor, "_execute", execute):
                            result = context(harness, first, [])
                        assert "短事务" in result.context_text
                        reused = sum(e.type == "tool_result_reused" for e in result.tool_events)
                        case.update(business_outcome="completed_evidence", duplicate_side_effect_count=0,
                            recovery_ready_ms=round((time.perf_counter()-resumed_at)*1000,2))
                    case["recovery_latency_ms"] = round((time.time()-prior["fault_epoch"])*1000,2)
                else:
                    calls = [harness.tool("A"), harness.tool("B", "workspace.files.list",
                        depends=[] if window == "parallel" else ["A"])]
                    with patch.object(ToolExecutor, "_execute", execute):
                        result = context(harness, first, calls)
                    assert "短事务" in result.context_text
                    case.update(business_outcome="completed_evidence", duplicate_side_effect_count=0,
                        recovery_ready_ms=None, recovery_latency_ms=None)
                event.remove(harness.engine, "before_cursor_execute", listener)
                with harness.sessions() as db:
                    steps = list(db.scalars(select(AgentStep).where(AgentStep.run_id==first.agent_id,AgentStep.sequence>0)))
                    sizes = [len(row.planner_state_json.encode()) for row in db.scalars(select(AgentCheckpoint).where(AgentCheckpoint.run_id==first.agent_id))]
                    case.update(artifact_reuse_count=reused, reusable_artifacts_at_fault=before_artifacts,
                        tool_requests=counts["tool_requests"], sql_statements=counts["sqlalchemy_statements"],
                        step_attempts=sum(s.attempts for s in steps), checkpoint_bytes=sum(sizes),
                        checkpoint_count=len(sizes), native_checkpoint_rows=db.scalar(text(
                            "SELECT count(*) FROM aiws_graph.checkpoints c JOIN aiws_graph.runtime_threads t USING(thread_id) WHERE t.run_id=:rid"),
                            {"rid":first.agent_id}), lease_wait_seconds=round(wait_seconds,2),
                        elapsed_ms=round((time.perf_counter()-started)*1000,2), model_requests=0, model_tokens=0)
                case["passed"] = True
            except Exception as exc:
                case["error_type"] = type(exc).__name__
                raise
            finally:
                if event.contains(harness.engine, "before_cursor_execute", listener):
                    event.remove(harness.engine, "before_cursor_execute", listener)
                report["cases"].append(case)
                save()
                harness.tearDown()
        normal = report["cases"][0]
        recovered = [c for c in report["cases"] if c["case"] in {"before_call","after_call","after_commit","before_checkpoint"}]
        for case in recovered:
            case["tool_request_overhead"] = case["tool_requests"]-normal["tool_requests"]
            case["business_sql_overhead"] = case["sql_statements"]-normal["sql_statements"]
        report["summary"] = {"cases_passed":len(report["cases"]), "read_recovery_successes":len(recovered),
            "read_recovery_cases":len(recovered), "reused_artifacts":sum(c["artifact_reuse_count"] for c in recovered),
            "committed_artifacts_at_fault":sum(c["reusable_artifacts_at_fault"] for c in recovered),
            "duplicate_side_effect_count":sum(c["duplicate_side_effect_count"] for c in report["cases"]),
            "native_sql_cost":None, "native_sql_cost_note":"psycopg查询未计量，保存行数另列，不捏造Token或费用"}
        report["passed"] = True
        save()
        lines = ["# 统一执行内核可靠性对照", "", report["scope"], "", report["cost_scope"], "",
            "| 场景 | 结果 | 工具实际调用 | 业务SQL | 等待租约秒 | 恢复就绪ms | 复用结果 |", "|---|---|---:|---:|---:|---:|---:|"]
        lines += [f"| {c['case']} | {c['business_outcome']} | {c['tool_requests']} | {c['sql_statements']} | {c['lease_wait_seconds']} | {c['recovery_ready_ms']} | {c['artifact_reuse_count']} |" for c in report["cases"]]
        output.with_suffix(".md").write_text("\n".join(lines)+"\n", encoding="utf-8")
        return report["summary"]
    finally:
        ChatToolRecoveryPostgresTest.tearDownClass()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--normal-repeats", type=int, default=0, help="只重复正常串行/并发两类场景，用于版本成本对照")
    args = parser.parse_args()
    if not 0 <= args.normal_repeats <= 20:
        parser.error("重复次数只能为0到20")
    print(json.dumps(run(args.output, normal_repeats=args.normal_repeats), ensure_ascii=False))
