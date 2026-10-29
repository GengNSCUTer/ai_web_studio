"""对固定题集并发调用真实答案模型与独立 Judge，逐题保存，失败不从分母删除。"""

from __future__ import annotations

import argparse
import json
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.knowledge import KnowledgeBase, KnowledgeEvalCase, KnowledgeEvalSet, KnowledgeDocument
from app.repositories.knowledge_repo import KnowledgeChunkRepository
from app.repositories.setting_repo import UserSettingRepository
from app.services.setting_service import SettingService
from app.services.chat_provider_service import resolve_provider_base_url
from app.services.knowledge_evaluation_service import KnowledgeEvaluationService
from app.services.knowledge_e2e_evaluation_service import KnowledgeE2EEvaluationConfig
from scripts.run_rag_e2e_eval import build_service, render_markdown


def evaluate_case(base_id, case_id, model, judge):
    with SessionLocal() as db:
        base=db.get(KnowledgeBase,base_id)
        case=db.get(KnowledgeEvalCase,case_id)
        service,settings=build_service(db)
        setting=settings.get_or_create_user_settings(base.user_id)
        if setting.provider_type not in {"openai-compatible","vllm"}:
            raise ValueError("本评测仅允许已配置的在线兼容API，不启动本地模型")
        runtime=dict(user_id=base.user_id,provider_type=setting.provider_type,
            base_url=resolve_provider_base_url(provider_type=setting.provider_type,
                configured_api_base_url=setting.api_base_url,configured_ollama_base_url=setting.ollama_base_url),
            api_key=settings.resolve_provider_api_key(base.user_id),model_name=model,judge_model_name=judge)
        config=KnowledgeEvaluationService._build_retrieval_config(knowledge_base=base,retrieval_mode="hybrid",rerank_enabled=True)
        # 外部网络等待前结束本次只读配置事务；每个线程独立 Session。
        db.expunge(case);db.rollback()
        result=service.run(**runtime,knowledge_base=config,cases=[case],config=KnowledgeE2EEvaluationConfig(top_k=20))
        row=result["cases"][0]
        row["question_type"] = next((t for t in json.loads(case.tags_json or "[]") if t in {
            "fact","mechanism","synthesis","comparison","procedure","numeric_condition","limitation","no_answer"}),"legacy_untyped")
        row.pop("error",None)
        return row


def run(args):
    with SessionLocal() as db:
        base=db.get(KnowledgeBase,args.base)
        if not base:raise ValueError("知识库不存在")
        eval_set=db.get(KnowledgeEvalSet,args.eval_set)
        if not eval_set or eval_set.user_id!=base.user_id or eval_set.knowledge_base_id!=base.id:
            raise ValueError("题集不属于指定知识库或用户")
        cases=list(db.scalars(select(KnowledgeEvalCase).where(KnowledgeEvalCase.eval_set_id==args.eval_set,
            KnowledgeEvalCase.user_id==base.user_id).order_by(KnowledgeEvalCase.created_at)))
        ids=[c.id for c in cases]
        name=base.name
        document_count=len(list(db.scalars(select(KnowledgeDocument.id).where(KnowledgeDocument.knowledge_base_id==base.id))))
        chunk_count=KnowledgeChunkRepository(db).count_by_knowledge_base(base.id,base.user_id,index_generation=base.active_index_generation)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    checkpoint=dict(eval_set_id=args.eval_set,knowledge_base_id=args.base,answer_model=args.model,judge_model=args.judge)
    previous=json.loads(args.output.read_text()) if args.output.exists() else {}
    if previous and any(previous.get(key)!=value for key,value in checkpoint.items()):
        raise ValueError("已有输出的题集或模型不同，不能混用续跑结果")
    rows=previous.get("cases",[])
    existing={r["case_id"] for r in rows}
    if not existing <= set(ids):raise ValueError("输出包含其他题集，不能混用")
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures={pool.submit(evaluate_case,args.base,c,args.model,args.judge):c for c in ids if c not in existing}
        for f in as_completed(futures):
            case_id=futures[f]
            try:row=f.result()
            except Exception as exc:row=dict(case_id=case_id,status="runner_failed",error_type=type(exc).__name__)
            rows.append(row)
            args.output.write_text(json.dumps(dict(**checkpoint,cases=rows),ensure_ascii=False,indent=2)+"\n")
            print(json.dumps(dict(completed=len(rows),total=len(ids),case_id=case_id,status=row["status"])),flush=True)
    rows.sort(key=lambda r:ids.index(r["case_id"]))
    scored=[r for r in rows if r["status"]=="scored"]
    def avg(items):return statistics.mean(items) if items else None
    metrics=dict(case_count=len(ids),scored_case_count=len(scored),
        retrieval_failure_count=sum(r["status"]=="retrieval_failed" for r in rows),
        generation_failure_count=sum(r["status"]=="generation_failed" for r in rows),
        judge_failure_count=sum(r["status"]=="judge_failed" for r in rows),
        runner_failure_count=sum(r["status"]=="runner_failed" for r in rows))
    for key in ("answer_faithfulness","answer_relevancy","citation_correctness"):
        metrics[key]=avg([r["judge"][key] for r in scored])
        metrics[key+"_all_cases"]=sum((r.get("judge") or {}).get(key) or 0 for r in rows)/len(ids)
    for key,field in (("citation_validity","valid_citation_rate"),("ground_truth_citation_precision","ground_truth_citation_precision"),
                      ("ground_truth_citation_recall","ground_truth_citation_recall")):
        metrics[key]=avg([r.get("citation_audit",{}).get(field,0) for r in rows])
    citation_count = sum(r.get("citation_audit", {}).get("citation_count", 0) for r in rows)
    valid_count = sum(r.get("citation_audit", {}).get("valid_citation_count", 0) for r in rows)
    metrics["citation_validity_micro"] = valid_count / citation_count if citation_count else None
    metrics["citation_coverage"] = sum(bool(r.get("citations")) for r in rows) / len(ids)
    metrics["answerable_case_count"] = sum(r.get("answerable", True) for r in rows)
    metrics["negative_case_count"] = sum(not r.get("answerable", True) for r in rows)
    metrics["by_answerability"] = {
        label: {key: avg([r["judge"][key] for r in scored if r.get("answerable", True) == flag])
                for key in ("answer_faithfulness", "answer_relevancy", "citation_correctness")}
        for label, flag in (("answerable", True), ("no_answer", False))
    }
    metrics["evaluation_policy"] = "v2_answerability_aware_judge"
    metrics["by_question_type"] = {kind:dict(case_count=sum(r.get("question_type")==kind for r in rows),
        scored_case_count=sum(r.get("question_type")==kind for r in scored),
        **{key:avg([r["judge"][key] for r in scored if r.get("question_type")==kind])
           for key in ("answer_faithfulness","answer_relevancy","citation_correctness")})
        for kind in sorted({r.get("question_type","legacy_untyped") for r in rows})}
    for key in ("retrieval_elapsed_ms","generation_elapsed_ms","judge_elapsed_ms"):
        metrics["avg_"+key]=avg([r[key] for r in rows if key in r])
    payload=dict(eval_set_id=args.eval_set,knowledge_base_id=args.base,case_count=len(ids),answer_model=args.model,
        judge_model=args.judge,top_k=20,retrieval_mode="hybrid",rerank_enabled=True,
        config_snapshot=dict(rerank_top_n=6,document_count=document_count,chunk_count=chunk_count,workers=args.workers),metrics=metrics,cases=rows)
    args.output.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n")
    args.output.with_suffix(".md").write_text(render_markdown(payload=payload,knowledge_base_name=name,document_count=document_count,chunk_count=chunk_count))
    print(json.dumps(metrics,ensure_ascii=False),flush=True)


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--base",required=True);p.add_argument("--eval-set",required=True)
    p.add_argument("--model",default="deepseek-ai/DeepSeek-V3.2")
    p.add_argument("--judge",default="Qwen/Qwen2.5-72B-Instruct")
    p.add_argument("--workers",type=int,choices=range(1,5),default=3)
    p.add_argument("--output",type=Path,required=True)
    run(p.parse_args())
