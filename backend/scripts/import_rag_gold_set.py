"""Import a reviewed RAG Gold Set manifest into one knowledge base.

The manifest uses stable file/Chunk selectors instead of copying database IDs.
New cases may provide ``expected_document_files`` and a list of
``expected_chunk_selectors``; each selector contains a document file name and a
reviewed marker.  The legacy singular fields remain supported.  The command
fails closed when a case has no target or a selector is ambiguous.
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


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _resolve_case(
    *,
    case: dict[str, Any],
    documents: list[Any],
    chunk_repo: KnowledgeChunkRepository,
    user_id: str,
    knowledge_base: Any,
) -> tuple[list[str], list[str]]:
    expected_document_ids = _as_list(case.get("expected_document_ids"))
    expected_chunk_ids = _as_list(case.get("expected_chunk_ids"))
    file_names = _as_list(case.get("expected_document_files"))
    legacy_file_name = str(case.get("expected_document_file") or "").strip()
    if legacy_file_name:
        file_names.insert(0, legacy_file_name)
    for file_name in dict.fromkeys(file_names):
        matches = [item for item in documents if item.file_name.casefold() == file_name.casefold()]
        if len(matches) != 1:
            raise ValueError(f"{case.get('case_id')}: expected_document_file 未唯一匹配：{file_name}")
        if matches[0].id not in expected_document_ids:
            expected_document_ids.append(matches[0].id)

    generation = knowledge_base.active_index_generation or "legacy"
    selectors = case.get("expected_chunk_selectors") or []
    if not isinstance(selectors, list):
        raise ValueError(f"{case.get('case_id')}: expected_chunk_selectors 必须是数组")
    for selector in selectors:
        if not isinstance(selector, dict):
            raise ValueError(f"{case.get('case_id')}: Chunk selector 必须是对象")
        file_name = str(selector.get("document_file") or "").strip()
        marker = str(selector.get("contains") or "").strip()
        chunk_index = selector.get("chunk_index")
        if not file_name or (not marker and chunk_index is None):
            raise ValueError(f"{case.get('case_id')}: Chunk selector 缺少 document_file 与 contains/chunk_index")
        doc_matches = [item for item in documents if item.file_name.casefold() == file_name.casefold()]
        if len(doc_matches) != 1:
            raise ValueError(f"{case.get('case_id')}: Chunk selector 文档未唯一匹配：{file_name}")
        document_id = doc_matches[0].id
        if document_id not in expected_document_ids:
            expected_document_ids.append(document_id)
        chunks = chunk_repo.list_by_document(document_id, user_id, index_generation=generation)
        if chunk_index is not None:
            try:
                selected_index = int(chunk_index)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{case.get('case_id')}: chunk_index 必须是整数") from exc
            matches = [item for item in chunks if item.chunk_index == selected_index]
            if marker:
                matches = [item for item in matches if marker.casefold() in (item.content or "").casefold()]
        else:
            matches = [item for item in chunks if marker.casefold() in (item.content or "").casefold()]
        if len(matches) != 1:
            raise ValueError(f"{case.get('case_id')}: Chunk selector 未唯一匹配：{file_name} / {marker}")
        if matches[0].id not in expected_chunk_ids:
            expected_chunk_ids.append(matches[0].id)

    # A marker list without per-document selectors is accepted only when every
    # marker resolves to exactly one Chunk in the selected active generation.
    for marker in _as_list(case.get("expected_chunk_contains")):
        candidates = []
        for document in documents:
            if expected_document_ids and document.id not in expected_document_ids:
                continue
            chunks = chunk_repo.list_by_document(document.id, user_id, index_generation=generation)
            candidates.extend(item for item in chunks if marker.casefold() in (item.content or "").casefold())
        if len(candidates) != 1:
            raise ValueError(f"{case.get('case_id')}: expected_chunk_contains 未唯一匹配：{marker}")
        if candidates[0].id not in expected_chunk_ids:
            expected_chunk_ids.append(candidates[0].id)

    for chunk_id in expected_chunk_ids:
        chunk = chunk_repo.get_by_user(chunk_id, user_id)
        if not chunk or chunk.knowledge_base_id != knowledge_base.id:
            raise ValueError(f"{case.get('case_id')}: expected_chunk_id 不属于当前知识库：{chunk_id}")
        if chunk.document_id not in expected_document_ids:
            expected_document_ids.append(chunk.document_id)

    if not expected_document_ids and not expected_chunk_ids:
        raise ValueError(f"{case.get('case_id')}: Gold Set Case 缺少人工标注目标")
    return expected_document_ids, expected_chunk_ids


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
        resolved_cases: list[tuple[dict[str, Any], list[str], list[str]]] = []
        for case in cases:
            if not isinstance(case, dict):
                raise SystemExit("Gold Set case must be an object")
            document_ids, chunk_ids = _resolve_case(
                case=case,
                documents=documents,
                chunk_repo=chunk_repo,
                user_id=args.user_id,
                knowledge_base=knowledge_base,
            )
            resolved_cases.append((case, document_ids, chunk_ids))

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
        for case, document_ids, chunk_ids in resolved_cases:
            service.add_eval_case(
                knowledge_base.id,
                eval_set.id,
                args.user_id,
                KnowledgeEvalCaseCreate(
                    query=str(case.get("query") or "").strip(),
                    expected_document_ids=document_ids,
                    expected_chunk_ids=chunk_ids,
                    expected_answer_keywords=[str(item) for item in case.get("expected_answer_keywords", [])],
                    difficulty=str(case.get("difficulty") or "").strip() or None,
                    tags=[str(item) for item in case.get("tags", [])],
                ),
            )
        print(json.dumps({"eval_set_id": eval_set.id, "case_count": len(resolved_cases)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
