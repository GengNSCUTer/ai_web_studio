"""分别评估五类动作与立即触发；支持不含正文的真实标注观测。"""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.services.memory_trigger_policy import POLICY_VERSION, classify_memory_signal  # noqa: E402

LABELS = ("extract_now", "revise", "revoke", "buffer", "skip")
IMMEDIATE = {"extract_now", "revise", "revoke"}


def metrics(rows):
    """触发分类不能以动作完全相等充当 TP；buffer 属于延后判断。"""
    def ratio(a, b):
        return round(a / b, 4) if b else None
    tp = sum(r["expected"] in IMMEDIATE and r["predicted"] in IMMEDIATE for r in rows)
    fp = sum(r["expected"] not in IMMEDIATE and r["predicted"] in IMMEDIATE for r in rows)
    fn = sum(r["expected"] in IMMEDIATE and r["predicted"] not in IMMEDIATE for r in rows)
    tn = len(rows) - tp - fp - fn
    by_label = {}
    for label in LABELS:
        correct = sum(r["expected"] == label == r["predicted"] for r in rows)
        by_label[label] = {"total": sum(r["expected"] == label for r in rows), "correct": correct,
            "precision": ratio(correct, sum(r["predicted"] == label for r in rows)),
            "recall": ratio(correct, sum(r["expected"] == label for r in rows))}
    return {"total": len(rows), "correct": sum(r["expected"] == r["predicted"] for r in rows),
        "action_accuracy": ratio(sum(r["expected"] == r["predicted"] for r in rows), len(rows)),
        "trigger_precision": ratio(tp, tp + fp), "trigger_recall": ratio(tp, tp + fn),
        "false_trigger_rate": ratio(fp, fp + tn), "miss_rate": ratio(fn, tp + fn),
        "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn}, "by_label": by_label,
        "errors": [r for r in rows if r["expected"] != r["predicted"]]}


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observations", type=Path, help="已标注真实观测 JSON；不需要消息正文")
    parser.add_argument("--dataset", type=Path, default=ROOT / "backend/evals/memory_trigger_labeled_cases.json")
    parser.add_argument("--output", type=Path, default=ROOT / "backend/evals/results/memory-trigger-policy-review-v1.json")
    args = parser.parse_args()
    dataset = json.loads((args.observations or args.dataset).read_text(encoding="utf-8"))
    rows = []
    for case in dataset["cases"]:
        expected = "extract_now" if case["expected"] == "extract" else case["expected"]
        prediction = case["predicted"] if args.observations else classify_memory_signal(case["text"]).action
        if expected not in LABELS or prediction not in LABELS:
            raise ValueError("invalid_label")
        rows.append({**case, "expected": expected, "predicted": prediction})
    report = {"dataset_version": dataset["dataset_version"], "source": dataset["source"],
        "policy_version": POLICY_VERSION, "evaluation": "frozen_observations" if args.observations else "rules_only_replay",
        **metrics(rows)}
    # 调用量单独按 Job 去重：多条来源可共享同一次语义请求，不能逐行相加。
    report["additional_semantic_call_attempts"] = len({r["job_id"] for r in rows
        if r.get("semantic_model_called") and r.get("job_id")}) if args.observations else 0
    report["model_call_scope"] = "application_reserved_attempts_not_provider_billing" if args.observations else "offline_rules_no_model_invoked"
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in {"errors", "by_label"}}, ensure_ascii=False))


if __name__ == "__main__":
    run()
