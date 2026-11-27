"""用固定标注语料评估各 Embedding 配置的长期记忆选择。"""

import argparse
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

from app.core.database import SessionLocal
from app.repositories.setting_repo import UserSettingRepository
from app.repositories.user_repo import UserRepository
from app.services.knowledge_index_service import KnowledgeEmbeddingService
from app.services.knowledge_model_metadata import infer_embedding_dimensions
from app.services.memory_injection_policy import lexical_evidence
from app.services.memory_retrieval_service import (
    MemoryRetrievalService, _FrozenEmbeddingSettings, cosine, embedding_config, valid_vector,
)
from app.services.memory_service import MemoryService
from app.services.setting_service import SettingService


def corpus():
    path = Path(__file__).resolve().parents[1] / "backend/evals/memory_retrieval_cases.tsv"
    memories, cases = [], []
    for line in path.read_text().splitlines():
        if not line or line.startswith("#"):
            continue
        scope, title, content, literal, paraphrase, english, negative = line.split("\t")
        index = len(memories)
        memories.append(SimpleNamespace(scope=scope if scope != "personal" else None, title=title,
                                       content=content, memory_type="fact"))
        for query, relevant in ((literal, {index}), (paraphrase, {index}), (english, {index}), (negative, set())):
            cases.append(("holdout" if index % 2 else "development", scope, query, relevant))
    return memories, cases


def route(scores, minimum):
    ordered = sorted((index for index in scores if scores[index] >= minimum), key=lambda index: (-scores[index], index))
    return {key: .5 * 61 / (60 + rank) for rank, key in enumerate(ordered[:32], 1)}


def evaluate(scored, threshold, strategy, weight=.5):
    precision = recall = irrelevant = 0
    relevant_cases = 0
    for lexical, semantic, expected in scored:
        lex_rank, sem_rank = route(lexical, .000001), route(semantic, threshold)
        ranks = {}
        for key in lex_rank.keys() | sem_rank.keys():
            rrf = lex_rank.get(key, 0) + sem_rank.get(key, 0)
            weighted = (1 - weight) * lexical.get(key, 0) * (key in lex_rank) + weight * (
                max(0., (semantic.get(key, 0) - threshold) / (1 - threshold)) if key in sem_rank else 0.)
            ranks[key] = rrf if strategy == "rrf" else weighted if strategy == "weighted" else (weighted + rrf) / 2
        top = set(sorted(ranks, key=lambda key: (-ranks[key], key))[:3])
        matched = len(top & expected)
        precision += matched / len(top) if top else float(not expected)
        irrelevant += bool(top - expected)
        if expected:
            relevant_cases += 1
            recall += matched / len(expected)
    return {"precision_at_3": precision / len(scored), "recall_at_3": recall / relevant_cases,
            "irrelevant_injection_rate": irrelevant / len(scored)}


def selection_report(scored):
    holdout, development = scored["holdout"], scored["development"]
    baseline = evaluate(holdout, .55, "rrf")
    comparisons = {"rrf": {"threshold": .55, **baseline}}
    for strategy in ("weighted", "hybrid"):
        def utility(params):
            result = evaluate(development, *params)
            return (result["precision_at_3"] + result["recall_at_3"] - result["irrelevant_injection_rate"],
                    result["recall_at_3"], params[0])
        threshold, _, weight = max(((t / 100, strategy, w) for t in range(35, 86, 5) for w in (.3, .5, .7)), key=utility)
        result = evaluate(holdout, threshold, strategy, weight)
        acceptable = (result["precision_at_3"] >= baseline["precision_at_3"]
                      and result["recall_at_3"] >= baseline["recall_at_3"]
                      and result["irrelevant_injection_rate"] <= baseline["irrelevant_injection_rate"]
                      and result != baseline)
        comparisons[strategy] = {"threshold": threshold, "semantic_weight": weight, **result, "passes_holdout": acceptable}
    return comparisons


async def run(email, alternate_models):
    memories, cases = corpus()
    with SessionLocal() as db:
        user = UserRepository(db).get_by_email(email)
        setting_repo = UserSettingRepository(db)
        setting = setting_repo.get_by_user(user.id) if user else None
        if not setting or setting.knowledge_embedding_provider == "ollama":
            raise ValueError("评测账号缺少在线 Embedding 配置")
        user_id = user.id
        config = embedding_config(setting)
        config["api_key"] = SettingService(setting_repo).resolve_knowledge_model_api_key(user_id, "embedding")
        if not config["api_key"]:
            raise ValueError("评测账号没有已保存的 Embedding 凭证")
    texts = [f"{row.title}\n{row.content}" for row in memories] + [case[2] for case in cases]
    results = []
    for model in dict.fromkeys([config["model"], *alternate_models]):
        model_config = {**config, "model": model, "dimensions": infer_embedding_dimensions(model, config["dimensions"])}
        adapter = KnowledgeEmbeddingService(_FrozenEmbeddingSettings(model_config))
        vectors = []
        try:
            for start in range(0, len(texts), 24):
                vectors.extend(await adapter.embed_texts(user_id=user_id,
                    knowledge_base=SimpleNamespace(embedding_provider=config["provider"], embedding_model=model),
                    texts=texts[start:start + 24]))
            if len(vectors) != len(texts) or not all(valid_vector(vector, model_config["dimensions"]) for vector in vectors):
                raise ValueError("invalid_embedding")
        except Exception as exc:
            results.append({"model": model, "error": type(exc).__name__, "status_code": getattr(exc, "status_code", None)})
            continue
        scored = {"holdout": [], "development": []}
        for case_index, (split, scope, query, expected) in enumerate(cases):
            eligible = {index for index, row in enumerate(memories) if row.scope in {None, scope}}
            scores = {index: cosine(vectors[index], vectors[len(memories) + case_index]) for index in eligible}
            lexical = {index: lexical_evidence(memories[index], query, MemoryService._search_terms) for index in eligible}
            scored[split].append((lexical, scores, expected))
        results.append({"model": model, "signature": MemoryRetrievalService.signature(model_config),
                        "strategies": selection_report(scored)})
    print(json.dumps({"cases": len(cases), "holdout": len(cases) // 2, "models": results}, ensure_ascii=False, indent=2))
    return any("strategies" in row for row in results)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", required=True)
    parser.add_argument("--model", action="append", default=[])
    args = parser.parse_args()
    raise SystemExit(0 if asyncio.run(run(args.account, args.model)) else 1)
