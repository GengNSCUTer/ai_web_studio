"""两库最终评测并列展示，正例检索和无答案/答案评分分开，不混合语料分母。"""
import argparse
import json
from pathlib import Path

from scripts.build_rag_100 import QUOTAS,TYPES


def run(args):
    root=Path(__file__).resolve().parents[1]/"evals"
    reports={}
    review_coverage=[]
    lines=["# 两库各100道中文问题：检索与答案验收", "",
        "英文论文库：中文问题检索英文资料；中文资料库：中文问题检索中文资料。每库95道可回答题和5道资料不足题，"
        "检索正例指标仅以95题为分母，无答案题单独看拒答/答案质量，不把无答案题召回相关但不能回答的块当成正例命中。", "",
        "| 题型 | 每库题数 | 检查范围 |", "| --- | ---: | --- |"]
    for kind,count in QUOTAS.items():lines.append(f"| {TYPES[kind].split('：')[0]} | {count} | {TYPES[kind].split('：')[1]} |")
    for corpus,label in [("paper","英文论文库"),("chinese","中文后端资料库")]:
        state=json.loads((root/"results"/f"{corpus}_100_state_20261028.json").read_text())
        if not state.get("matrix_complete") or not state.get("e2e_complete"):raise ValueError("本库还有阶段未完成")
        fixture=json.loads((root/f"{corpus}_100_chinese_20261028.json").read_text())
        common=json.loads((root/"results"/f"{corpus}_100_20261028_common6.json").read_text())
        original=json.loads((root/"results"/f"{corpus}_100_20261028_e2e.json").read_text())
        review_path=root/"results"/f"{corpus}_100_20261028_rejudged.json"
        e2e=json.loads(review_path.read_text()) if args.rejudged else original
        if args.rejudged:
            original_rows={r["case_id"]:r for r in original["cases"]}
            if e2e.get("eval_set_id")!=original["eval_set_id"] or len(e2e["cases"])!=len(original_rows):
                raise ValueError("复评不属于原完整题集")
            for row in e2e["cases"]:
                old=original_rows.get(row["case_id"])
                if not old or any(row[k]!=old[k] for k in ["query","answer","citations"]):
                    raise ValueError("复评不得更改问题、答案或引用")
        if len(fixture["cases"])!=100 or e2e["case_count"]!=100:raise ValueError("分母不是100")
        m=e2e["metrics"];reports[corpus]=dict(state=state,coverage=fixture["coverage"],retrieval=common,e2e_metrics=m,
            original_e2e_metrics=original["metrics"],rejudge_policy=e2e.get("rejudge_policy"))
        review_coverage.append(f"{label}{m['scored_case_count']}/100")
        lines += ["",f"## {label}","",f"知识库 `{state['base']}`，题集 `{state['eval_set_id']}`。",
            f"{e2e['config_snapshot']['document_count']}文档、{e2e['config_snapshot']['chunk_count']}活动块；"
            f"标注覆盖{len(fixture['coverage']['documents'])}个文档，{fixture['coverage']['multi_chunk_cases']}题含多个不同Ground-Truth Chunk。", "",
            "| 模式 | Hit@6 | MRR@6 | nDCG@6 | Precision@6 | Recall@6 | 均时ms | 失败 |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
        for name,g in common["groups"].items():
            r=g["common_at_6"];native=g["native_metrics"]
            lines.append("| "+name+" | "+" | ".join(f"{r[k]*100:.2f}%" for k in ["hit_at_k","mrr","ndcg_at_k","context_precision","context_recall"])
                +f" | {native['avg_elapsed_ms']:.1f} | {native.get('failure_count',0)} |")
        lines += ["",f"100题中{m['scored_case_count']}题完成独立评分；检索/生成/Judge/运行器失败分别为"
            f"{m['retrieval_failure_count']}/{m['generation_failure_count']}/{m['judge_failure_count']}/{m['runner_failure_count']}。",
            "", "| 答案指标 | 已完成评审均值 | 全100题（失败计0） | 可回答题已评审均值 | 无答案题已评审均值 |", "| --- | ---: | ---: | ---: | ---: | ---: |"]
        if args.rejudged:
            lines.insert(len(lines)-3,
                f"原始评审{original['metrics']['scored_case_count']}/100；本次所有原答案统一复评，"
                f"共{m['judge_attempt_count']}次应用层尝试，{m['judge_retry_case_count']}题使用重试。原问题、答案、引用及证据顺序不变；未重检索或重新生成答案。")
        def score(value):return f"{value*100:.2f}/100" if value is not None else "未完成"
        for key,title in [("answer_faithfulness","忠实度"),("answer_relevancy","问题相关性"),("citation_correctness","引用正确性")]:
            lines.append(f"| {title} | {score(m[key])} | {score(m[key+'_all_cases'])} | {score(m['by_answerability']['answerable'][key])} | {score(m['by_answerability']['no_answer'][key])} |")
        lines += ["", "### Hybrid+Rerank分题型", "",
            "| 题型 | 检索正例题数 | Hit@6 | Recall@6 | 答案计分题数 | 忠实度 | 相关性 | 引用正确性 |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
        subgroup=common["groups"]["hybrid_rerank"]["by_question_type"]
        for kind in QUOTAS:
            ret=subgroup.get(kind,{})
            ans=m["by_question_type"].get(kind,{})
            pct=lambda k:f"{ret[k]*100:.2f}%" if ret.get(k) is not None else "不计正例"
            lines.append(f"| {kind} | {ret.get('case_count',0)} | {pct('hit_at_k')} | {pct('context_recall')} | {ans.get('scored_case_count',0)} | "
                +" | ".join(score(ans.get(k)) for k in ["answer_faithfulness","answer_relevancy","citation_correctness"])+" |")
        lines += ["", "答案模型为在线DeepSeek-V3.2，独立评审Qwen2.5-72B；评审分数是模型辅助评分，不等同于人工精确正确率。"
            "全部题目在本轮检索前完成原文审核、逐字绑定与去重；属于维护者构造的固定验收集，非外部专家盲测。"
            "精确Chunk标注未穷尽全部等价证据，低Precision不能直接解释为其余块全部无用。",
            "检索请求top-k=20；两路内部可各取40候选，成功精排最多6，所有检索组按实际返回前6统一计分。"
            "正文多证据题不代表实现了模型多轮自主检索；本轮未改RAG检索策略以追高新题分数。"]
    lines += ["", "## 本轮结论与限制", "",
        "两库各100题均完成检索及真实答案生成；当前独立评分覆盖："+"、".join(review_coverage)+"。"
        "因此已评分均值不能称为全量答案准确率；失败计0一列只表示验收链路覆盖折损，不代表失败答案经人工判定为0分。"
        "所有失败仍在原始JSON分母内；没有删除失败行后重算，也没有替换问题或增补命中标签。", "",
        "在相同百题集上Vector+Rerank优于Hybrid+Rerank：英文Hit@6为82.11%对67.37%，中文100%对98.95%。"
        "英文库纯词法Hit@6为24.21%，弱于向量70.53%，提示应进一步检查跨语言词法候选与融合排序；本轮未据此改生产检索配置。"
        "英文综合题的Hybrid+Rerank Recall@6为46.67%，仍存在多块证据遗漏。新100题与历史90/16题不同，不构成严格前后性能对比。", "",
        "Judge失败样本的单独诊断发现：解释中的LaTeX反斜杠未按JSON转义，导致严格解析失败；"
        "不能据此把全部51个失败都归为同一原因。原始失败行只保留judge_failed代码与部分解析状态，"
        "没有保留全部原始评审响应，因此不能虚构更细的历史异常分布。当前修复使用JSON模式、简短说明、最多3次格式/瞬时故障重试，"
        "401/403等永久错误不重试；严格校验字段和数值，不从无效JSON补造分数。"
        "旧来源只有800字符预览，本次按相同活动版本、原ID和顺序从数据库恢复正文并逐条匹配预览；"
        "不能将这种重建称为旧完整快照的密码学证明。新的执行和复评文件均保存完整证据、脱敏响应诊断和每次尝试。", "",
        "简历中的检索与答案指标分别注明配置：Vector+Rerank的Hit/MRR/Recall以各95道可回答题为分母；"
        "Hybrid+Rerank的答案评审以固定100题为分母，不拼接成同一配置的端到端表现。"
        "工具任务修复与测试另见[189报告](189_两库百题与工具任务失败修复_2026-10-28.md)。"]
    args.output.write_text("\n".join(lines)+"\n")
    args.output.with_suffix(".json").write_text(json.dumps(reports,ensure_ascii=False,indent=2)+"\n")


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--output",type=Path,required=True)
    p.add_argument("--rejudged",action="store_true",help="采用完成的同答案复评，保留原始评分作对照")
    run(p.parse_args())
