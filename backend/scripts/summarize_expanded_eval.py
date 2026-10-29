"""从持久化检索结果重算统一 @6 指标，不重新检索或修改目标证据。"""

import argparse
import json
import statistics
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.knowledge import KnowledgeEvalCase, KnowledgeEvalResult
from app.services.knowledge_evaluation_service import KnowledgeEvaluationService


def summarize(matrix_path: Path, output: Path):
    matrix=json.loads(matrix_path.read_text())
    old=json.loads((Path(__file__).resolve().parents[1]/"evals/rag_gold_set.json").read_text())
    old_queries={c["query"] for c in old["cases"]}
    groups={}
    with SessionLocal() as db:
        for label,run in matrix["comparison"].items():
            rows=[]
            for result in db.scalars(select(KnowledgeEvalResult).where(KnowledgeEvalResult.run_id==run["run_id"])):
                records=json.loads(result.retrieved_json or "[]")
                targets=json.loads(result.expected_chunk_ids_json or "[]")
                case=db.get(KnowledgeEvalCase,result.case_id)
                if case and "no-answer" in json.loads(case.tags_json or "[]"):
                    # 负例没有正例证据，只保留原生报告中的数量和返回非空率。
                    continue
                if not targets:
                    raise ValueError("目标块失效或未标注，不能混入精确块评测")
                objects=[SimpleNamespace(chunk=SimpleNamespace(id=r["chunk_id"],document_id=r["document_id"],content=r["content"])) for r in records[:6]]
                score=KnowledgeEvaluationService._score_case(retrieved=objects,expected_chunk_ids=targets)
                documents=set(json.loads(result.expected_document_ids_json or "[]"))
                question_type=next((t for t in json.loads(case.tags_json or "[]") if t in {
                    "fact","mechanism","synthesis","comparison","procedure","numeric_condition","limitation"}),"legacy_untyped")
                rows.append(dict(case_id=result.case_id,query=result.query,question_type=question_type,
                    bucket="legacy20" if result.query in old_queries else "new70",
                    returned=len(records),returned_at_6=len(objects),gt_chunks=len(targets),
                    rerank_candidates=next((r.get("metadata",{}).get("rerank_candidate_count") for r in records
                        if r.get("metadata",{}).get("rerank_candidate_count") is not None),None),
                    document_hit_at_6=bool(documents.intersection(o.chunk.document_id for o in objects)),**score))
            def mean(group,key):return statistics.mean(float(r[key]) for r in group) if group else None
            keys=["hit_at_k","mrr","ndcg_at_k","context_precision","context_recall","document_hit_at_6"]
            groups[label]=dict(run_id=run["run_id"],native_metrics=run["metrics"],
                common_at_6={key:mean(rows,key) for key in keys},
                by_question_type={kind:dict(case_count=sum(r["question_type"]==kind for r in rows),
                    **{key:mean([r for r in rows if r["question_type"]==kind],key) for key in keys})
                    for kind in sorted({r["question_type"] for r in rows})},
                buckets={bucket:{key:mean([r for r in rows if r["bucket"]==bucket],key) for key in keys} for bucket in ("legacy20","new70")},cases=rows)
    payload=dict(eval_set_id=matrix["eval_set_id"],requested_top_k=matrix["runs"][0]["top_k"],comparison_cutoff=6,
        metric_scope="可回答题精确块ID二元相关性；未穷尽所有正确证据；统一截取每组实际结果前6条；无答案题单独见原生报告",groups=groups)
    output.write_text(json.dumps(payload,ensure_ascii=False,indent=2)+"\n")
    lines=["# 固定资料库检索指标（统一 @6）","","请求 top-k=20，重排最终最多6条；实际精排输入可能扩展，逐题数量见JSON的rerank_candidates。所有组统一在返回列表前6条计分。", "",
        "| 配置 | Hit@6 | MRR@6 | nDCG@6 | Context Precision@6 | Context Recall@6 | Doc Hit@6 | 平均检索ms | 失败 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for label,g in groups.items():
        m=g["common_at_6"];native=g["native_metrics"]
        lines.append("| "+label+" | "+" | ".join(f"{m[k]*100:.2f}%" for k in keys)+f" | {native['avg_elapsed_ms']:.1f} | {native.get('failure_count',0)} |")
    lines += ["", "逐题、题型分组和原生返回规模指标见同名 JSON；原始结果保存在 PostgreSQL KnowledgeEvalRun/Result。兼容保留的 legacy20/new70 字段只表示是否出现在历史题集，不表示本题集的题数。",
        "标注在检索前冻结，属于维护者模型辅助构造及来源复查，不是外部专家盲测；没有根据本次检索得分增补命中目标。"]
    output.with_suffix(".md").write_text("\n".join(lines)+"\n")
    print(json.dumps({k:v["common_at_6"] for k,v in groups.items()},ensure_ascii=False))


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--matrix",type=Path,required=True);p.add_argument("--output",type=Path,required=True)
    a=p.parse_args();summarize(a.matrix,a.output)
