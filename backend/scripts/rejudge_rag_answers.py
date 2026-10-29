"""复用固定答案和原证据顺序统一复评，不重新检索、生成答案或修改Gold。"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
import json
from pathlib import Path
import statistics
from threading import Lock
from time import monotonic, sleep

from app.core.database import SessionLocal
from app.models.knowledge import KnowledgeBase, KnowledgeChunk, KnowledgeEvalCase
from app.services.chat_provider_service import resolve_provider_base_url
from app.services.knowledge_e2e_evaluation_service import KnowledgeE2EEvaluationConfig
from scripts.run_rag_e2e_eval import build_service, render_markdown


def restore_sources(row, *, load_chunk, user_id, base_id, generation):
    """旧报告只有预览：按原ID/顺序重建，范围或预览不符即拒绝，不换证据。"""
    config = KnowledgeE2EEvaluationConfig()
    sources = []
    used = 0
    for stored in row["retrieved"]:
        chunk = load_chunk(stored["chunk_id"])
        if (not chunk or chunk.user_id != user_id or chunk.knowledge_base_id != base_id
                or chunk.document_id != stored["document_id"] or chunk.chunk_index != stored["chunk_index"]
                or chunk.index_generation != generation):
            raise ValueError("原始证据不存在或用户/知识库/文档/版本不一致")
        metadata = json.loads(chunk.metadata_json or "{}")
        current = str(metadata.get("parent_content") or chunk.content or "")
        current = current[:min(config.max_chunk_chars, max(0, config.max_context_chars-used))]
        content = stored.get("content", current)
        if not current or content != current or content[:800] != stored["content_preview"]:
            raise ValueError("原始证据预览或完整快照不一致，不能静默改评审材料")
        sources.append({k:v for k,v in stored.items() if k != "content_preview"} | {"content": content})
        used += len(content)
    return sources


def review_case(payload, row, pacer=None):
    if pacer:
        pacer()
    with SessionLocal() as db:
        base = db.get(KnowledgeBase, payload["knowledge_base_id"])
        if not base:
            raise ValueError("原知识库不存在")
        case = db.get(KnowledgeEvalCase, row["case_id"])
        if (not case or case.user_id != base.user_id or case.eval_set_id != payload["eval_set_id"]
                or case.query != row["query"]):
            raise ValueError("原题集、用户或问题已变化")
        sources = restore_sources(row, load_chunk=lambda key: db.get(KnowledgeChunk,key),
            user_id=base.user_id, base_id=base.id, generation=base.active_index_generation)
        service, settings = build_service(db)
        setting = settings.get_or_create_user_settings(base.user_id)
        if setting.provider_type not in {"openai-compatible", "vllm"}:
            raise ValueError("复评只使用已配置的在线兼容API")
        runtime = dict(provider_type=setting.provider_type,
            base_url=resolve_provider_base_url(provider_type=setting.provider_type,
                configured_api_base_url=setting.api_base_url, configured_ollama_base_url=setting.ollama_base_url),
            api_key=settings.resolve_provider_api_key(base.user_id))
        # 完整输入复制后归还数据库连接，网络等待不占用事务。
        db.rollback()
        result = service.judge_answer(**runtime, judge_model_name=payload["judge_model"],
            query=row["query"], answer=row["answer"], citations=row["citations"], sources=sources,
            answerable=row["answerable"])
    reviewed = deepcopy(row)
    reviewed["previous_evaluation"] = {k:row[k] for k in ["status","judge","error_code","judge_elapsed_ms"] if k in row}
    reviewed.pop("error_code",None)
    reviewed.update(result)
    reviewed["retrieved"] = [dict(s, content_preview=s["content"][:800]) for s in sources]
    reviewed["source_snapshot_origin"] = "database_reconstruction_with_matching_original_preview"
    return reviewed


def summarize_review(payload):
    """只重算评审项；检索、答案及其耗时沿用原执行，所有失败保留在分母。"""
    rows = payload["cases"]
    if len(rows) != payload["case_count"]:
        raise ValueError("复评分母与原题数不同")
    scored = [r for r in rows if r["status"] == "scored"]
    metrics = deepcopy(payload["metrics"])
    metrics["scored_case_count"] = len(scored)
    metrics["judge_failure_count"] = sum(r["status"] == "judge_failed" for r in rows)
    metrics["runner_failure_count"] = sum(r["status"] == "runner_failed" for r in rows)
    keys = ("answer_faithfulness", "answer_relevancy", "citation_correctness")
    def avg(items): return statistics.mean(items) if items else None
    for key in keys:
        metrics[key] = avg([r["judge"][key] for r in scored])
        metrics[key+"_all_cases"] = sum((r.get("judge") or {}).get(key) or 0 for r in rows)/len(rows)
    metrics["by_answerability"] = {name: {key: avg([r["judge"][key] for r in scored if r["answerable"] == flag])
        for key in keys} for name,flag in [("answerable",True),("no_answer",False)]}
    metrics["by_question_type"] = {kind:dict(case_count=sum(r["question_type"] == kind for r in rows),
        scored_case_count=sum(r["question_type"] == kind for r in scored),
        **{key:avg([r["judge"][key] for r in scored if r["question_type"] == kind]) for key in keys})
        for kind in sorted({r["question_type"] for r in rows})}
    metrics["avg_judge_elapsed_ms"] = avg([r.get("judge_elapsed_ms",0) for r in rows])
    metrics["judge_retry_case_count"] = sum(len(r.get("judge_attempts",[])) > 1 or bool(r.get("prior_rejudge_attempts")) for r in rows)
    metrics["judge_attempt_count"] = sum(len(r.get("judge_attempts",[])) + sum(
        len(old.get("judge_attempts") or []) for old in r.get("prior_rejudge_attempts",[])) for r in rows)
    return metrics


def run(args):
    if args.interval < 0:
        raise ValueError("请求间隔不能为负数")
    original = json.loads(args.input.read_text())
    if args.input.resolve() == args.output.resolve():
        raise ValueError("不能覆盖原始评测")
    if any(not r.get("answer") or r["status"] not in {"scored","judge_failed"} for r in original["cases"]):
        raise ValueError("仅对已生成答案的完整报告复评")
    payload = deepcopy(original)
    previous = json.loads(args.output.read_text()) if args.output.exists() else {}
    if previous and (previous.get("eval_set_id") != original["eval_set_id"] or previous.get("judge_model") != original["judge_model"]):
        raise ValueError("已有复评输出不属于同一题集/评审模型")
    original_by_id = {r["case_id"]:r for r in original["cases"]}
    rows = previous.get("cases",[])
    if len({r["case_id"] for r in rows}) != len(rows):
        raise ValueError("复评记录含重复题")
    for row in rows:
        original_row = original_by_id.get(row["case_id"])
        if not original_row or any(row[k] != original_row[k] for k in ["query","answer","citations"]):
            raise ValueError("续跑记录改变了原问题或答案")
    if args.retry_failed:
        for row in rows:
            if row["status"] != "scored":
                original_by_id[row["case_id"]] = deepcopy(original_by_id[row["case_id"]])
                original_by_id[row["case_id"]]["prior_rejudge_attempts"] = [
                    *row.get("prior_rejudge_attempts",[]),
                    {k:row.get(k) for k in ["status","judge_attempts","error_type"]}]
        rows = [r for r in rows if r["status"] == "scored"]
    existing = {r["case_id"] for r in rows}
    payload["rejudge_policy"] = {"version":"json_mode_short_explanation_v1", "all_cases_rejudged":True,
        "source_report":args.input.name, "answer_regenerated":False, "retrieval_rerun":False,
        "judge_max_attempts":3, "judge_max_tokens":1000, "selection":"first_valid_response",
        "minimum_request_interval_seconds":args.interval}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    lock = Lock()
    next_request = 0.0
    def pace():
        nonlocal next_request
        with lock:
            delay = max(0,next_request-monotonic())
            if delay:
                sleep(delay)
            next_request = monotonic()+args.interval
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(review_case, original, row, pace):row for row in original_by_id.values() if row["case_id"] not in existing}
        for future in as_completed(futures):
            old = futures[future]
            try:
                row = future.result()
            except Exception as exc:
                row = deepcopy(old)
                row.update(status="runner_failed",error_type=type(exc).__name__)
                row.pop("judge",None)
            rows.append(row)
            payload["cases"] = rows
            # 中间文件只存进度，不保留原评审的旧汇总，防止误读为已完成。
            payload.pop("metrics",None)
            args.output.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n")
            print(json.dumps(dict(completed=len(rows),total=len(original["cases"]),case_id=row["case_id"],status=row["status"])),flush=True)
    rows.sort(key=lambda r:list(original_by_id).index(r["case_id"]))
    payload["metrics"] = deepcopy(original["metrics"])
    payload["metrics"] = summarize_review(payload)
    args.output.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n")
    args.output.with_suffix(".md").write_text(render_markdown(payload=payload,knowledge_base_name="原答案统一复评",
        document_count=payload["config_snapshot"]["document_count"],chunk_count=payload["config_snapshot"]["chunk_count"]))
    print(json.dumps(payload["metrics"],ensure_ascii=False),flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--workers",type=int,choices=range(1,5),default=2)
    parser.add_argument("--interval",type=float,default=3,help="本进程复评请求最小间隔秒数")
    parser.add_argument("--retry-failed",action="store_true",help="仅补先前失败项，保留尝试历史，已评分项不重打")
    run(parser.parse_args())
