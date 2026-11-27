"""用已有标注集与在线 Embedding 对照候选门槛，只读知识库和词法文件。"""

import argparse
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from sqlalchemy import select

from app.core.database import SessionLocal
from app.models.knowledge import KnowledgeBase, KnowledgeChunk, KnowledgeDocument
from app.repositories.setting_repo import UserSettingRepository
from app.repositories.user_repo import UserRepository
from app.services.knowledge_index_service import KnowledgeEmbeddingService, KnowledgeLexicalStore
from app.services.knowledge_read_runtime import FrozenReadSettings
from app.services.knowledge_relevance import query_anchors
from app.services.evidence_context import EvidenceSection, EvidenceUnit
from app.services.setting_service import SettingService


def ranked_ids(vector, words, floor, lexical):
    scores = {}
    for route in ([i for i, score in vector if not floor or score >= floor],
                  [i for i, coverage in words if coverage >= lexical]):
        for rank, idx in enumerate(route, 1):
            scores[idx] = scores.get(idx, 0.) + 1. / (60 + rank)
    return sorted(scores, key=scores.get, reverse=True)[:6]


def measure(cases, cosine, lexical):
    recalled = positives = covered = multi = false = negatives = 0
    for vector, words, expected, answerable, floor in cases:
        top = set(ranked_ids(vector, words, max(cosine, floor), lexical))
        if answerable and expected:
            positives += 1
            recalled += bool(top & expected)
            if len(expected) > 1:
                multi += 1
                covered += expected <= top
        elif not answerable:
            negatives += 1
            false += bool(top)
    return dict(recall=recalled / max(1, positives), irrelevant=false / max(1, negatives),
                coverage=covered / max(1, multi), positives=positives, negatives=negatives, multi=multi)


async def calibrate(account):
    root = Path(__file__).resolve().parents[1] / "evals"
    all_cases = {"development": [], "holdout": []}
    matched = []
    coverage_scores = {mode: {"any_support": 0, "all_support": 0} for mode in ("baseline", "candidate")}
    coverage_cases = 0
    for name, holdout in (("paper_100_chinese_20261028.json", "paper_rag_holdout_20261028.json"),
                          ("chinese_100_chinese_20261028.json", "chinese_rag_holdout_20261028.json")):
        first = json.loads((root / name).read_text())
        held = json.loads((root / holdout).read_text())["cases"]
        cases = [*first["cases"], *held]
        files = sorted({item["document_file"] for case in cases for item in case.get("expected_chunk_selectors", [])})
        held_files = {item["document_file"] for case in held for item in case.get("expected_chunk_selectors", [])}
        held_files.update(file for index, file in enumerate(files) if index % 3 == 0)
        with SessionLocal() as db:
            user = UserRepository(db).get_by_email(account)
            base = db.get(KnowledgeBase, first["knowledge_base_id"])
            if not user or not base or user.id != base.user_id:
                raise ValueError("评测库或已授权测试账号不可用")
            config = FrozenReadSettings(SettingService(UserSettingRepository(db)), user.id)
            user_id = user.id
            model = SimpleNamespace(id=base.id, embedding_provider=base.embedding_provider,
                embedding_model=base.embedding_model, embedding_dimensions=base.embedding_dimensions,
                generation=base.active_index_generation or "legacy", score_threshold=base.score_threshold)
            chunks = db.execute(select(KnowledgeChunk.vector_id, KnowledgeChunk.chunk_index,
                KnowledgeChunk.embedding, KnowledgeDocument.file_name, KnowledgeChunk.content).join(KnowledgeDocument,
                KnowledgeDocument.id == KnowledgeChunk.document_id).where(KnowledgeChunk.user_id == user.id,
                KnowledgeChunk.knowledge_base_id == base.id, KnowledgeChunk.index_generation == model.generation,
                KnowledgeChunk.embedding.is_not(None), KnowledgeChunk.embedding_model == model.embedding_model,
                KnowledgeChunk.embedding_dimensions == model.embedding_dimensions)).all()
        matrix = np.array([row.embedding for row in chunks], dtype=np.float64)
        norms = np.linalg.norm(matrix, axis=1)
        positions = {(row.file_name, row.chunk_index): row.vector_id for row in chunks}
        document_text = {row.vector_id: row.content for row in chunks}
        provider = KnowledgeEmbeddingService(config)
        store = KnowledgeLexicalStore()
        matched.append(model.embedding_model)
        for start in range(0, len(cases), 24):
            batch = cases[start:start + 24]
            vectors = await provider.embed_texts(user_id=user_id, knowledge_base=model,
                texts=[case["query"] for case in batch])
            for offset, (case, vector) in enumerate(zip(batch, vectors, strict=True)):
                exact = (matrix @ vector) / np.maximum(norms * np.linalg.norm(vector), 1e-12)
                vector_hits = [(chunks[idx].vector_id, float(exact[idx])) for idx in np.argsort(-exact)[:40]]
                lexical = store.search(knowledge_base_id=model.id, generation_id=model.generation,
                    query=case["query"], top_k=40)
                word_hits = [(hit.vector_id, hit.coverage) for hit in lexical]
                selectors = case.get("expected_chunk_selectors", [])
                case_files = {item["document_file"] for item in selectors}
                if case_files & held_files and case_files - held_files:
                    continue
                expected = {positions.get((item["document_file"], item["chunk_index"])) for item in selectors} - {None}
                held_case = bool(case_files & held_files) or start + offset >= len(first["cases"])
                all_cases["holdout" if held_case else "development"].append(
                    (vector_hits, word_hits, expected, case.get("answerable", True), model.score_threshold))
                if held_case and len(expected) > 1:
                    coverage_cases += 1
                    ids = ranked_ids(vector_hits, word_hits, model.score_threshold, 0.)
                    units = tuple(EvidenceUnit({"metadata": {}, "citation_label": f"[KB{idx}]"}, f"[KB{idx}]",
                        document_text[idx], len(document_text[idx])) for idx in ids)
                    passages = {positions.get((entry["document_file"], entry["chunk_index"])): entry.get("contains", "").strip()
                                for entry in selectors}
                    for mode, anchors in (("baseline", ()), ("candidate", query_anchors(case["query"]))):
                        section = EvidenceSection("knowledge_context", "", units, anchors).fit(lambda item: len(item.render()) <= 1800)
                        supplied = {int(unit.label[3:-1]) for unit in section.units if
                            passages.get(int(unit.label[3:-1])) and passages[int(unit.label[3:-1])] in unit.body}
                        coverage_scores[mode]["any_support"] += bool(supplied & expected)
                        coverage_scores[mode]["all_support"] += expected <= supplied
    training = all_cases["development"]
    held_out = all_cases["holdout"]
    if not all({model == matched[0] for model in matched}):
        raise ValueError("混合模型需单独评测")
    base = measure(held_out, 0., 0.)
    best = None
    best_score = None
    for cosine in (0., .25, .35, .45, .55, .65):
        for lexical in (0., .01, .03, .05, .10, .20):
            if not cosine and not lexical:
                continue
            values = measure(training, cosine, lexical)
            score = (values["recall"] - values["irrelevant"] + values["coverage"], values["recall"], -cosine, -lexical)
            if best_score is None or score > best_score:
                best_score, best = score, (cosine, lexical)
    result = measure(held_out, *best)
    passes = (result["recall"] >= base["recall"] and result["irrelevant"] <= base["irrelevant"]
              and result["coverage"] >= base["coverage"] and result != base)
    coverage_passes = (coverage_scores["candidate"]["any_support"] >= coverage_scores["baseline"]["any_support"]
                       and coverage_scores["candidate"]["all_support"] > coverage_scores["baseline"]["all_support"])
    return dict(model=matched[0], development=len(training), holdout=len(held_out), baseline=base,
                candidate=dict(cosine=best[0], lexical=best[1], **result), passes=passes,
                evidence_coverage=dict(cases=coverage_cases, **coverage_scores, passes=coverage_passes))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--account", required=True)
    print(json.dumps(asyncio.run(calibrate(parser.parse_args().account)), ensure_ascii=False, indent=2))
