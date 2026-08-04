"""Run the fixed RAG retrieval ablation matrix for one persisted Gold Set.

The script deliberately accepts an existing ``eval_set_id``.  It never creates
labels or invents metrics; the Gold Set must already be reviewed and bound to
real document/Chunk IDs in the selected knowledge base.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from app.core.database import SessionLocal
from app.core.startup import ensure_runtime_schema
from app.repositories.knowledge_repo import (
    KnowledgeBaseRepository,
    KnowledgeChunkRepository,
    KnowledgeDocumentRepository,
    KnowledgeEvalCaseRepository,
    KnowledgeEvalResultRepository,
    KnowledgeEvalRunRepository,
    KnowledgeEvalSetRepository,
)
from app.repositories.setting_repo import UserSettingRepository
from app.services.knowledge_evaluation_service import KnowledgeEvaluationService
from app.services.setting_service import SettingService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knowledge-base-id", required=True)
    parser.add_argument("--eval-set-id", required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--top-k", type=int)
    parser.add_argument("--output", help="Optional JSON output path")
    return parser.parse_args()


def build_service(db: Any) -> KnowledgeEvaluationService:
    return KnowledgeEvaluationService(
        base_repo=KnowledgeBaseRepository(db),
        chunk_repo=KnowledgeChunkRepository(db),
        eval_set_repo=KnowledgeEvalSetRepository(db),
        eval_case_repo=KnowledgeEvalCaseRepository(db),
        eval_run_repo=KnowledgeEvalRunRepository(db),
        eval_result_repo=KnowledgeEvalResultRepository(db),
        setting_service=SettingService(UserSettingRepository(db)),
        document_repo=KnowledgeDocumentRepository(db),
    )


def main() -> int:
    args = parse_args()
    ensure_runtime_schema()
    with SessionLocal() as db:
        outcome = build_service(db).run_eval_matrix(
            args.knowledge_base_id,
            args.eval_set_id,
            args.user_id,
            top_k=args.top_k,
        )
        if outcome is None:
            raise SystemExit("knowledge base or evaluation set not found")
        payload = {
            "eval_set_id": outcome.eval_set_id,
            "runs": [run.model_dump(mode="json") for run in outcome.runs],
            "comparison": outcome.comparison,
        }
        rendered = json.dumps(payload, ensure_ascii=False, indent=2, default=str)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as handle:
                handle.write(rendered + "\n")
        print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
