"""Import a reviewed RAG Gold Set manifest into one knowledge base.

The manifest uses stable file/Chunk selectors instead of copying database IDs:
``expected_document_file`` resolves a document by its uploaded file name and
``expected_chunk_contains`` resolves the unique active-generation Chunk that
contains a reviewed marker.  The command fails closed when a case has no target
or a selector is ambiguous.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
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
from app.schemas.knowledge import KnowledgeEvalCaseCreate, KnowledgeEvalSetCreate
from app.services.knowledge_evaluation_service import KnowledgeEvaluationService
from app.services.setting_service import SettingService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--knowledge-base-id", required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument(
        "--fixture",
        default="evals/rag_gold_set.json",
        help="Gold Set JSON path relative to backend/",
    )
    parser.add_argument("--name", help="Override the persisted evaluation-set name")
    return parser.parse_args()


def _build_service(db: Any) -> KnowledgeEvaluationService:
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


def _resolve_case(
    *,
    case: dict[str, Any],
    documents: list[Any],
    chunk_repo: KnowledgeChunkRepository,
    user_id: str,
    knowledge_base: Any,
) -> tuple[str | None, str | None]:
    expected_document_id = str(case.get("expected_document_id") or "").strip() or None
    expected_chunk_id = str(case.get("expected_chunk_id") or "").strip() or None
    file_name = str(case.get("expected_document_file") or "").strip()
    if file_name:
        matches = [item for item in documents if item.file_name.casefold() == file_name.casefold()]
        if len(matches) != 1:
            raise ValueError(f"{case.get('case_id')}: expected_document_file 未唯一匹配：{file_name}")
        expected_document_id = matches[0].id

    marker = str(case.get("expected_chunk_contains") or "").strip()
    if marker:
        if not expected_document_id:
            raise ValueError(f"{case.get('case_id')}: expected_chunk_contains 必须先绑定文档")
        generation = knowledge_base.active_index_generation or "legacy"
        chunks = chunk_repo.list_by_document(
            expected_document_id,
            user_id,
            index_generation=generation,
        )
        matches = [item for item in chunks if marker in (item.content or "")]
        if len(matches) != 1:
            raise ValueError(f"{case.get('case_id')}: expected_chunk_contains 未唯一匹配：{marker}")
        expected_chunk_id = matches[0].id

    if expected_chunk_id and not expected_document_id:
        raise ValueError(f"{case.get('case_id')}: expected_chunk_id 必须同时绑定文档")
    if not expected_document_id and not expected_chunk_id:
        raise ValueError(f"{case.get('case_id')}: Gold Set Case 缺少人工标注目标")
    return expected_document_id, expected_chunk_id


def main() -> int:
    args = parse_args()
    fixture_path = Path(args.fixture)
    if not fixture_path.is_absolute():
        fixture_path = Path(__file__).resolve().parent.parent / fixture_path
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    cases = fixture.get("cases") if isinstance(fixture, dict) else None
    if not isinstance(cases, list) or not cases:
        raise SystemExit("Gold Set fixture must contain a non-empty cases array")

    ensure_runtime_schema()
    with SessionLocal() as db:
        base_repo = KnowledgeBaseRepository(db)
        knowledge_base = base_repo.get_by_user(args.knowledge_base_id, args.user_id)
        if not knowledge_base:
            raise SystemExit("knowledge base not found")
        documents = KnowledgeDocumentRepository(db).list_by_knowledge_base(
            knowledge_base.id,
            args.user_id,
        )
        chunk_repo = KnowledgeChunkRepository(db)
        resolved_cases: list[tuple[dict[str, Any], str | None, str | None]] = []
        for case in cases:
            if not isinstance(case, dict):
                raise SystemExit("Gold Set case must be an object")
            document_id, chunk_id = _resolve_case(
                case=case,
                documents=documents,
                chunk_repo=chunk_repo,
                user_id=args.user_id,
                knowledge_base=knowledge_base,
            )
            resolved_cases.append((case, document_id, chunk_id))

        service = _build_service(db)
        eval_set = service.create_eval_set(
            knowledge_base.id,
            args.user_id,
            KnowledgeEvalSetCreate(
                name=args.name or str(fixture.get("name") or "rag_gold_v1"),
                description=str(fixture.get("description") or "Reviewed RAG Gold Set"),
            ),
        )
        if not eval_set:
            raise SystemExit("failed to create evaluation set")
        for case, document_id, chunk_id in resolved_cases:
            service.add_eval_case(
                knowledge_base.id,
                eval_set.id,
                args.user_id,
                KnowledgeEvalCaseCreate(
                    query=str(case.get("query") or "").strip(),
                    expected_document_id=document_id,
                    expected_chunk_id=chunk_id,
                    expected_answer_keywords=[str(item) for item in case.get("expected_answer_keywords", [])],
                    difficulty=str(case.get("difficulty") or "").strip() or None,
                    tags=[str(item) for item in case.get("tags", [])],
                ),
            )
        print(json.dumps({"eval_set_id": eval_set.id, "case_count": len(resolved_cases)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
