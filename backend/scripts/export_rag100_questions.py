"""把已审核的两套题集导出为按题型查阅的中文题目与证据手册。"""
import argparse
from collections import Counter
import json
from pathlib import Path

from scripts.build_rag_100 import TYPES, QUOTAS


def run(args):
    fixtures = [(label,json.loads(path.read_text())) for label,path in [
        ("英文论文库：中文提问、英文资料",args.paper),("中文资料库：中文提问、中文资料",args.chinese)]]
    lines=["# 两个知识库各100道中文测试题", "",
           "本手册只收录先按原文构造、绑定真实文档/Chunk并审核完成的题集；不按检索得分筛题。"
           "可回答题与无答案题分别计分，题型标签不代表所有现实场景均已覆盖。", "",
           "| 题型 | 每库题数 | 检查内容 |", "| --- | ---: | --- |"]
    for kind,count in QUOTAS.items():lines.append(f"| {kind} | {count} | {TYPES[kind]} |")
    for label,fixture in fixtures:
        cases=fixture["cases"]
        if fixture.get("ready_for_evaluation") is not True or len(cases)!=100:
            raise ValueError("不能把尚未审核完的草稿导出为最终题集")
        if Counter(c["question_type"] for c in cases)!=Counter(QUOTAS):raise ValueError("题型数量不符")
        lines += ["",f"## {label}","",f"知识库：`{fixture['knowledge_base_id']}`；版本：`{fixture['index_generation']}`。",
                  "", f"覆盖统计：{json.dumps(fixture['coverage'],ensure_ascii=False)}。"]
        for kind in QUOTAS:
            lines += ["",f"### {TYPES[kind].split('：')[0]}",""]
            for case in [c for c in cases if c["question_type"]==kind]:
                lines += [f"#### {case['case_id']}：{case['query']}", "",
                          "预期要点："+"；".join(case.get("expected_answer_claims",[])), ""]
                for target in case.get("expected_chunk_selectors",[]):
                    quote=target["contains"].replace("\n"," ")
                    lines.append(f"- `{target['document_file']}`，Chunk {target['chunk_index']}：{quote}")
                if not case["answerable"]:lines.append("无正例文档/Chunk，检查是否如实说明资料不足。")
                lines.append("")
    args.output.write_text("\n".join(line.rstrip() for line in lines).rstrip()+"\n")


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--paper",type=Path,required=True);p.add_argument("--chinese",type=Path,required=True)
    p.add_argument("--output",type=Path,required=True)
    run(p.parse_args())
