"""按已有失败断言分类历史回归；不改原答案、标注、成绩或宣称当前复跑。"""

import argparse
from collections import Counter
import json
from pathlib import Path


def classify_check(check: str) -> str:
    if check.startswith(("cited_file:", "gold_chunk", "knowledge_cited", "citations_")):
        return "citation_or_gold_coverage"
    if check in {"correct_diff", "one_pending_approval", "honest_refusal_or_incomplete"}:
        return "edit_approval_or_honest_status"
    if check in {"only_direct_file_read", "correct_file_argument", "call_count_within_limit"} or check.startswith("required_group"):
        return "planning_or_arguments"
    if check.startswith("fact_"):
        return "answer_fact_or_annotation_review"
    if check in {"chat_done", "stream_and_database_done"}:
        return "chat_lifecycle"
    if check == "forbidden_claims_absent":
        return "safety_or_literal_judge_review"
    return "unclassified_requires_review"


def analyze(report: dict, *, source: str) -> dict:
    cases = report["cases"]
    rows = []
    for case in cases:
        if case["status"] == "passed":
            continue
        failed = [key for key, value in case.get("checks", {}).items() if value is False]
        rows.append({"id": case["id"], "status": case["status"], "failed_checks": failed,
            "gap_labels": sorted({classify_check(key) for key in failed}) or ["unclassified_requires_review"]})
    return {"source": source, "source_commit": report.get("git_commit"),
        "scope": "历史断言离线分类，不是当前版本重新执行；标签不等于根因证明，不修改成绩",
        "statuses": dict(Counter(case["status"] for case in cases)),
        "gap_case_counts": dict(Counter(label for row in rows for label in row["gap_labels"])),
        "label_counts_overlap": True,
        "cases": rows}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    path = Path(args.report)
    result = analyze(json.loads(path.read_text(encoding="utf-8")), source=str(path))
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "cases"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
