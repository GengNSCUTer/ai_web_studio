"""按已核对的原文修订标签，生成独立 v2 文件；不读取检索结果或覆盖 v1。"""

import argparse
from copy import deepcopy
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "evals"


def chinese() -> dict:
    fixture = json.loads((ROOT / "chinese_backend_rag_gold_set.json").read_text())
    fixture = deepcopy(fixture)
    # 目标依据按演示文稿真实顺序解析的原文核对，不参考检索排名。
    revisions = {
        "redis_01": [("01-Redis快速入门.pptx", 2, "低延迟")],
        "redis_02": [("01-Redis快速入门.pptx", 3, "EXPIRE")],
        "redis_03": [("01-Redis快速入门.pptx", 4, "MGET")],
        "redis_04": [("01-Redis快速入门.pptx", 6, "HSET key field")],
        "redis_05": [("02-Redis企业实战.pptx", 22, "set nx ex")],
        "redis_06": [("02-Redis企业实战.pptx", 21, "KEYS数组")],
        "redis_07": [("02-Redis企业实战.pptx", 22, "不可重入"),
                     ("02-Redis企业实战.pptx", 29, "超时续约")],
        "redis_08": [("02-Redis企业实战.pptx", 21, "获取锁中的线程标示")],
        "python_02": [("python教程.pdf", 5, "第8章 探讨")],
        "python_03": [("python教程.pdf", 63, "2.4　序列数据结构")],
    }
    for case in fixture["cases"]:
        case["answerable"] = not case["case_id"].startswith("negative_")
        if not case["answerable"]:
            case.pop("expected_chunk_selectors", None)
            case["review_note"] = "全文未给出问题要求的统一保证/配置，不能绑定任意相关段落充当答案。"
        elif case["case_id"] in revisions:
            case["expected_chunk_selectors"] = [dict(document_file=name, chunk_index=index, contains=marker)
                                                for name, index, marker in revisions[case["case_id"]]]
            case["review_note"] = "按原文复核：正文优先，去除冗余目录证据；Redis 使用真实幻灯片顺序。"
    fixture.update(schema_version=2, name="chinese_backend_rag_v2",
                   description="原16题原问题保留；修正无答案标注、正文定位、Redisson证据以及PPTX重建后的目标。诊断集，非留出盲测。")
    fixture["labeling_policy"] = dict(source_first=True, retrieval_blind=False,
        review="维护者基于原文修订，已知v1失败类型；未使用排名自动补标签",
        answerable_cases=sum(c["answerable"] for c in fixture["cases"]),
        negative_cases=sum(not c["answerable"] for c in fixture["cases"]),
        multiple_evidence_cases=sum(len(c.get("expected_chunk_selectors", [])) > 1 for c in fixture["cases"]))
    return fixture


def papers() -> dict:
    fixture = deepcopy(json.loads((ROOT / "rag_expanded_20261027.json").read_text()))
    for case in fixture["cases"]:
        case["answerable"] = True
        selectors = case["expected_chunk_selectors"]
        if case["case_id"] == "rag_multi_adaptive_classifier_001":
            case["expected_chunk_selectors"] = [dict(document_file="Adaptive_RAG.pdf", chunk_index=i, contains=marker)
                for i, marker in [(33, "Query Complexity Assessment"), (34, "Training Strategy"), (35, "crossentropy")]]
            case["review_note"] = "删除与训练无关的评测指标块45，补齐训练标签来源34和35；33说明类别与推理策略。"
        elif case["query"].startswith("AT-RAG模型在数据预处理阶段"):
            case["expected_chunk_selectors"] = [dict(document_file="AT-RAG.pdf", chunk_index=18, contains="BERTopic"),
                dict(document_file="AT-RAG.pdf", chunk_index=20, contains="Topic assign")]
            case["review_note"] = "18说明主题模型，20说明标签概率入元数据；21主要是向量存储和答案评分，去除。"
        if case["query"].startswith(("论文提出的", "论文中评估的", "为什么论文认为")):
            case["query"] = f"在《{Path(selectors[0]['document_file']).stem}》中，" + case["query"]
            case["review_note"] = case.get("review_note", "") + " 原题缺少论文主体，显式补文档名，不替换核心问题。"
    fixture.update(schema_version=5, name="paper_rag_reviewed_v2_20261028",
                   description="保留90题；修正两题的必要证据和三题缺失文档主体。其余继承原标注，不宣称已穷尽所有可接受证据。")
    fixture["labeling_policy"]["v2_review"] = "来源正文复核；开发诊断集，非全新盲测；不覆盖历史结果"
    return fixture


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", choices=["chinese", "papers"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(chinese() if args.corpus == "chinese" else papers(), ensure_ascii=False, indent=2) + "\n")
