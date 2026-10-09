"""在同一知识库和 Gold Set 上对比上下文 Query Rewrite 的检索效果。"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

from sqlalchemy import select


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.core.database import SessionLocal  # noqa: E402
from app.models.knowledge import KnowledgeBase, KnowledgeEvalCase, KnowledgeEvalSet  # noqa: E402
from app.repositories.knowledge_repo import KnowledgeChunkRepository  # noqa: E402
from app.repositories.setting_repo import UserSettingRepository  # noqa: E402
from app.services.chat_provider_service import resolve_provider_base_url  # noqa: E402
from app.services.knowledge_query_rewriter import KnowledgeQueryRewriteService  # noqa: E402
from app.services.knowledge_retrieval_pipeline import KnowledgeRetrievalPipeline  # noqa: E402
from app.services.setting_service import SettingService  # noqa: E402


CASES = ROOT / "backend" / "evals" / "rag_contextual_rewrite_cases.json"
EVAL_SET_NAME = "rag_gold_v3_20_case"
KNOWLEDGE_BASE_NAME = "论文库"


async def evaluate(mode: str) -> dict[str, object]:
    cases = json.loads(CASES.read_text(encoding="utf-8"))
    with SessionLocal() as db:
        knowledge_base = db.scalar(select(KnowledgeBase).where(KnowledgeBase.name == KNOWLEDGE_BASE_NAME))
        if knowledge_base is None:
            raise RuntimeError("评测知识库不存在。")
        eval_set = db.scalar(
            select(KnowledgeEvalSet).where(
                KnowledgeEvalSet.knowledge_base_id == knowledge_base.id,
                KnowledgeEvalSet.name == EVAL_SET_NAME,
            )
        )
        if eval_set is None:
            raise RuntimeError("固定 RAG Gold Set 不存在。")
        gold_cases = {
            item.query: item
            for item in db.scalars(select(KnowledgeEvalCase).where(KnowledgeEvalCase.eval_set_id == eval_set.id))
        }
        setting_service = SettingService(UserSettingRepository(db))
        pipeline = KnowledgeRetrievalPipeline(
            chunk_repo=KnowledgeChunkRepository(db),
            setting_service=setting_service,
        )
        rewriter = KnowledgeQueryRewriteService()
        provider_config = setting_service.get_or_create_user_settings(knowledge_base.user_id)
        api_key = setting_service.resolve_provider_api_key(knowledge_base.user_id)
        base_url = resolve_provider_base_url(
            provider_type=provider_config.provider_type,
            configured_api_base_url=provider_config.api_base_url,
            configured_ollama_base_url=provider_config.ollama_base_url,
        )

        outcomes: list[dict[str, object]] = []
        for case in cases:
            query = case["followup"]
            messages = [{"id": f"{case['id']}-previous", "role": "user", "content": case["previous_user"]}]
            rewrite_started = time.perf_counter()
            if mode == "baseline":
                rewrite = rewriter.rewrite(query=query, recent_messages=messages)
            else:
                rewrite = await rewriter.rewrite_async(
                    query=query,
                    recent_messages=messages,
                    provider_type=provider_config.provider_type,
                    base_url=base_url,
                    api_key=api_key,
                    model_name=provider_config.default_model,
                )
            rewrite_ms = round((time.perf_counter() - rewrite_started) * 1000, 1)
            outcome: dict[str, object] = {
                "id": case["id"],
                "rewrite": rewrite.rewritten_query,
                "strategy": rewrite.strategy,
                "rewrite_ms": rewrite_ms,
            }
            if case.get("expect_unchanged") and not case.get("source_query"):
                outcome["unchanged_correct"] = not rewrite.did_rewrite
                outcomes.append(outcome)
                continue
            source_case = gold_cases.get(case.get("source_query"))
            if source_case is None:
                raise RuntimeError(f"Gold Set 缺少 Case：{case['id']}")
            expected_ids = set(json.loads(source_case.expected_chunk_ids_json or "[]"))
            retrieval_started = time.perf_counter()
            try:
                results = await pipeline.retrieve_async(
                    user_id=knowledge_base.user_id,
                    knowledge_base=knowledge_base,
                    query=rewrite.rewritten_query,
                    top_k=knowledge_base.retrieval_top_k,
                )
                retrieved_ids = [item.chunk.id for item in results]
                ranks = [rank for rank, chunk_id in enumerate(retrieved_ids, 1) if chunk_id in expected_ids]
                outcome.update(
                    hit=bool(ranks),
                    mrr=round(1 / ranks[0], 4) if ranks else 0.0,
                    recall=round(len(ranks) / len(expected_ids), 4) if expected_ids else 0.0,
                    retrieved=len(retrieved_ids),
                )
            except Exception as exc:
                outcome.update(hit=False, mrr=0.0, recall=0.0, error_type=type(exc).__name__)
            outcome["retrieval_ms"] = round((time.perf_counter() - retrieval_started) * 1000, 1)
            if case.get("expect_unchanged"):
                outcome["unchanged_correct"] = not rewrite.did_rewrite
            outcomes.append(outcome)

        scored = [item for item in outcomes if "hit" in item]
        controls = [item for item in outcomes if "unchanged_correct" in item]
        return {
            "mode": mode,
            "knowledge_base_id": knowledge_base.id,
            "index_generation": knowledge_base.active_index_generation,
            "eval_set_id": eval_set.id,
            "scored_cases": len(scored),
            "hit_at_final_n": round(sum(bool(item["hit"]) for item in scored) / len(scored), 4),
            "mrr_at_final_n": round(statistics.mean(float(item["mrr"]) for item in scored), 4),
            "recall_at_final_n": round(statistics.mean(float(item["recall"]) for item in scored), 4),
            "unchanged_control_accuracy": round(
                sum(bool(item["unchanged_correct"]) for item in controls) / len(controls), 4
            ),
            "mean_rewrite_ms": round(statistics.mean(float(item["rewrite_ms"]) for item in outcomes), 1),
            "mean_retrieval_ms": round(statistics.mean(float(item.get("retrieval_ms", 0)) for item in scored), 1),
            "cases": outcomes,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("baseline", "candidate"), required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(evaluate(args.mode)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
