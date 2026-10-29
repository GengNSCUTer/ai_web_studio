"""只读追踪现有题集的召回、融合、精排和阈值阶段，不修改知识库或标签。"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.knowledge import KnowledgeBase, KnowledgeEvalCase
from app.repositories.knowledge_repo import KnowledgeChunkRepository
from app.repositories.setting_repo import UserSettingRepository
from app.services.knowledge_evaluation_service import KnowledgeEvaluationService
from app.services.knowledge_retrieval_pipeline import KnowledgeRetrievalPipeline
from app.services.setting_service import SettingService


async def diagnose(args: argparse.Namespace) -> None:
    with SessionLocal() as db:
        base = db.get(KnowledgeBase, args.base)
        if base is None:
            raise ValueError("知识库不存在")
        user_id = base.user_id
        config = KnowledgeEvaluationService._build_retrieval_config(
            knowledge_base=base, retrieval_mode="hybrid", rerank_enabled=True,
        )
        rows = list(db.scalars(select(KnowledgeEvalCase).where(
            KnowledgeEvalCase.eval_set_id == args.eval_set,
            KnowledgeEvalCase.knowledge_base_id == base.id,
            KnowledgeEvalCase.user_id == user_id,
        ).order_by(KnowledgeEvalCase.created_at)))
        cases = [dict(id=row.id, query=row.query,
                      targets=KnowledgeEvaluationService._case_chunk_ids(row),
                      tags=json.loads(row.tags_json or "[]")) for row in rows]
        if not cases:
            raise ValueError("题集不存在或不属于该知识库")
        db.rollback()
        pipeline = KnowledgeRetrievalPipeline(
            chunk_repo=KnowledgeChunkRepository(db),
            setting_service=SettingService(UserSettingRepository(db)),
        )
        original_vector = pipeline._retrieve_vector_results
        original_lexical = pipeline._retrieve_lexical_results
        original_rerank = pipeline._rerank_if_needed
        original_model = pipeline.rerank_service.rerank
        output = dict(knowledge_base_id=args.base, eval_set_id=args.eval_set,
                      requested_top_k=args.top_k, score_threshold=config.score_threshold,
                      rerank_top_n=config.rerank_top_n, cases=[])

        for case in cases:
            trace: dict = dict(case_id=case["id"], query=case["query"],
                               target_ids=case["targets"], no_answer="no-answer" in case["tags"])

            def compact(items):
                return [dict(chunk_id=item.chunk.id, document_id=item.chunk.document_id,
                             chunk_index=item.chunk.chunk_index,
                             score=item.rerank_score if item.rerank_score is not None else item.score)
                        for item in items]

            async def vector(**kwargs):
                items = await original_vector(**kwargs)
                trace["vector"] = compact(items)
                return items

            def lexical(**kwargs):
                items = original_lexical(**kwargs)
                trace["lexical"] = compact(items)
                return items

            async def rerank(**kwargs):
                trace["fused"] = compact(kwargs["results"])
                return await original_rerank(**kwargs)

            async def model(**kwargs):
                ranked = await original_model(**kwargs)
                pool = trace.get("fused", [])
                trace["reranked_before_threshold"] = [
                    dict(**{key: value for key, value in pool[index].items() if key != "score"}, score=score)
                    for index, score in ranked if 0 <= index < len(pool)
                ]
                return ranked

            try:
                with patch.object(pipeline, "_retrieve_vector_results", vector), \
                     patch.object(pipeline, "_retrieve_lexical_results", lexical), \
                     patch.object(pipeline, "_rerank_if_needed", rerank), \
                     patch.object(pipeline.rerank_service, "rerank", model):
                    result = await pipeline.retrieve_async(
                        user_id=user_id, knowledge_base=config, query=case["query"], top_k=args.top_k,
                    )
                trace["final"] = compact(result)
                trace["status"] = "ok"
            except Exception as exc:
                trace["status"] = "failed"
                trace["error_type"] = type(exc).__name__
            output["cases"].append(trace)
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(json.dumps(dict(completed=len(output["cases"]), total=len(cases), status=trace["status"])), flush=True)
            db.rollback()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--eval-set", required=True)
    parser.add_argument("--top-k", type=int, choices=range(1, 101), default=20)
    parser.add_argument("--output", type=Path, required=True)
    asyncio.run(diagnose(parser.parse_args()))
