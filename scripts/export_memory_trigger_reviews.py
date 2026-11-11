"""导出本人已标注的真实触发观测；不读取或导出消息正文/凭证。"""

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app.core.database import SessionLocal  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.memory_trigger_decision import MemoryTriggerDecision  # noqa: E402
from app.services.memory_trigger_review_service import owned_decisions  # noqa: E402
from sqlalchemy import select  # noqa: E402


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", required=True, help="仅本机管理员导出指定拥有者的人工标注")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    with SessionLocal() as db:
        if db.scalar(select(User.id).where(User.id == args.user_id)) is None:
            raise ValueError("user_not_found")
        rows = list(db.scalars(owned_decisions(args.user_id).where(
            MemoryTriggerDecision.expected_action.is_not(None)).order_by(
                MemoryTriggerDecision.created_at.desc(), MemoryTriggerDecision.id.desc()).limit(10000)))
        # 同一消息版本跨轮重评只保留最新人工标注，避免缓冲反复观测抬高分母。
        seen, cases = set(), []
        for row in rows:
            identity = (row.message_id, row.source_digest, row.policy_version)
            if identity in seen:
                continue
            seen.add(identity)
            cases.append({"id": row.id, "policy_version": row.policy_version,
                "expected": row.expected_action, "predicted": row.action, "review_reason": row.review_reason,
                "outcome": row.outcome, "job_id": row.job_id,
                "semantic_model_called": row.semantic_model_called,
                "semantic_action": row.semantic_action})
    args.output.write_text(json.dumps({"dataset_version": "reviewed-observations-v1",
        "source": "owner_reviewed_real_decisions_no_raw_text", "cases": cases},
        ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"exported_labeled_observations": len(cases)}))


if __name__ == "__main__":
    run()
