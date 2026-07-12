from __future__ import annotations

import asyncio
import json
import math
from collections import Counter
from dataclasses import dataclass, field

from app.models.knowledge import KnowledgeBase
from app.repositories.knowledge_repo import KnowledgeChunkRepository
from app.services.knowledge_index_service import (
    KnowledgeEmbeddingService,
    KnowledgeFaissStore,
    LexicalSearchHit,
    KnowledgeLexicalStore,
    KnowledgeRerankService,
    RetrievalResult,
)
from app.services.setting_service import SettingService


@dataclass(frozen=True)
class KnowledgeRetrievalFilter:
    document_ids: list[str] = field(default_factory=list)
    file_types: list[str] = field(default_factory=list)
    page_start: int | None = None
    page_end: int | None = None
    section_query: str | None = None

    @property
    def enabled(self) -> bool:
        return bool(
            self.document_ids
            or self.file_types
            or self.page_start is not None
            or self.page_end is not None
            or self.section_query
        )

    def to_public_dict(self) -> dict[str, object]:
        return {
            "document_ids": self.document_ids,
            "file_types": self.file_types,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "section_query": self.section_query,
            "enabled": self.enabled,
        }


class KnowledgeRetrievalPipeline:
    """Owns query-time retrieval orchestration.

    Index construction stays in KnowledgeIndexService; RAG-6 retrieval quality
    features should depend on this pipeline instead of growing the index service.
    """

    def __init__(
        self,
        *,
        chunk_repo: KnowledgeChunkRepository,
        setting_service: SettingService,
        embedding_service: KnowledgeEmbeddingService | None = None,
        rerank_service: KnowledgeRerankService | None = None,
        faiss_store: KnowledgeFaissStore | None = None,
        lexical_store: KnowledgeLexicalStore | None = None,
    ) -> None:
        self.chunk_repo = chunk_repo
        self.setting_service = setting_service
        self.embedding_service = embedding_service or KnowledgeEmbeddingService(setting_service)
        self.rerank_service = rerank_service or KnowledgeRerankService(setting_service)
        self.faiss_store = faiss_store or KnowledgeFaissStore()
        self.lexical_store = lexical_store or KnowledgeLexicalStore()

    def retrieve(
        self,
        *,
        user_id: str,
        knowledge_base: KnowledgeBase,
        query: str,
        top_k: int,
        filters: KnowledgeRetrievalFilter | None = None,
    ) -> list[RetrievalResult]:
        return asyncio.run(
            self.retrieve_async(
                user_id=user_id,
                knowledge_base=knowledge_base,
                query=query,
                top_k=top_k,
                filters=filters,
            )
        )

    async def retrieve_async(
        self,
        *,
        user_id: str,
        knowledge_base: KnowledgeBase,
        query: str,
        top_k: int,
        filters: KnowledgeRetrievalFilter | None = None,
    ) -> list[RetrievalResult]:
        filters = filters or KnowledgeRetrievalFilter()
        retrieval_mode = (knowledge_base.retrieval_mode or "vector").strip().lower()
        if retrieval_mode == "lexical":
            results = self._retrieve_lexical_results(
                user_id=user_id,
                knowledge_base=knowledge_base,
                query=query,
                top_k=top_k,
                filters=filters,
            )
            return await self._rerank_if_needed(
                user_id=user_id,
                knowledge_base=knowledge_base,
                query=query,
                results=results,
            )
        if retrieval_mode == "hybrid":
            try:
                vector_results = await self._retrieve_vector_results(
                    user_id=user_id,
                    knowledge_base=knowledge_base,
                    query=query,
                    top_k=self._candidate_top_k(top_k=top_k, filters=filters),
                    filters=filters,
                )
                vector_error = None
            except Exception as exc:
                vector_results = []
                vector_error = str(exc)
            lexical_results = self._retrieve_lexical_results(
                user_id=user_id,
                knowledge_base=knowledge_base,
                query=query,
                top_k=self._candidate_top_k(top_k=top_k, filters=filters),
                filters=filters,
            )
            results = self._fuse_rrf(
                vector_results=vector_results,
                lexical_results=lexical_results,
                top_k=top_k,
                vector_error=vector_error,
            )
            return await self._rerank_if_needed(
                user_id=user_id,
                knowledge_base=knowledge_base,
                query=query,
                results=results,
            )
        results = await self._retrieve_vector_results(
            user_id=user_id,
            knowledge_base=knowledge_base,
            query=query,
            top_k=top_k,
            filters=filters,
        )
        return await self._rerank_if_needed(
            user_id=user_id,
            knowledge_base=knowledge_base,
            query=query,
            results=results,
        )

    async def _retrieve_vector_results(
        self,
        *,
        user_id: str,
        knowledge_base: KnowledgeBase,
        query: str,
        top_k: int,
        filters: KnowledgeRetrievalFilter,
    ) -> list[RetrievalResult]:
        query_vector = (
            await self.embedding_service.embed_texts(
                user_id=user_id,
                knowledge_base=knowledge_base,
                texts=[query],
            )
        )[0]
        self._validate_vectors(vectors=[query_vector], expected_count=1, dimensions=knowledge_base.embedding_dimensions)
        candidate_top_k = self._candidate_top_k(top_k=top_k, filters=filters)
        index_generation = self._active_generation(knowledge_base)
        hits = self.faiss_store.search(
            knowledge_base_id=knowledge_base.id,
            query_vector=query_vector,
            top_k=candidate_top_k,
            generation_id=index_generation,
        )
        if not hits:
            return []

        hit_vector_ids = [vector_id for vector_id, _ in hits]
        score_by_id = {vector_id: score for vector_id, score in hits}
        chunks = self.chunk_repo.list_by_vector_ids_and_knowledge_base(
            knowledge_base_id=knowledge_base.id,
            user_id=user_id,
            vector_ids=hit_vector_ids,
            index_generation=index_generation,
        )
        chunk_by_vector_id = {chunk.vector_id: chunk for chunk in chunks}
        results: list[RetrievalResult] = []
        for vector_id, score in hits:
            chunk = chunk_by_vector_id.get(vector_id)
            if not chunk:
                continue
            metadata = json.loads(chunk.metadata_json or "{}")
            results.append(RetrievalResult(chunk=chunk, score=score_by_id[vector_id], metadata=metadata))
        if filters.enabled:
            return self._apply_filters(results, filters)[:top_k]
        return results[:top_k]

    async def _rerank_if_needed(
        self,
        *,
        user_id: str,
        knowledge_base: KnowledgeBase,
        query: str,
        results: list[RetrievalResult],
    ) -> list[RetrievalResult]:
        if not results:
            return []
        if not knowledge_base.rerank_enabled or len(results) <= 1:
            return self._apply_score_threshold(results, knowledge_base.score_threshold)
        if knowledge_base.rerank_top_n <= 0:
            return self._apply_score_threshold(results, knowledge_base.score_threshold)

        rerank_top_n = min(knowledge_base.rerank_top_n, len(results))
        try:
            reranked = await self.rerank_service.rerank(
                user_id=user_id,
                knowledge_base=knowledge_base,
                query=query,
                documents=[result.chunk.content for result in results],
                top_n=rerank_top_n,
            )
        except Exception as exc:
            return self._apply_score_threshold(
                self._mark_rerank_fallback(results, str(exc)),
                knowledge_base.score_threshold,
            )
        if not reranked:
            return self._apply_score_threshold(results, knowledge_base.score_threshold)

        result_by_index = {index: result for index, result in enumerate(results)}
        reranked_results: list[RetrievalResult] = []
        for index, rerank_score in reranked:
            result = result_by_index.get(index)
            if not result:
                continue
            metadata = {
                **result.metadata,
                "rerank_model": knowledge_base.rerank_model,
                "pre_rerank_score": result.score,
            }
            if "vector_score" not in metadata and result.rank_source in {"vector", "vector_fallback"}:
                metadata["vector_score"] = result.score
            reranked_results.append(
                RetrievalResult(
                    chunk=result.chunk,
                    score=result.score,
                    rerank_score=rerank_score,
                    rank_source="rerank",
                    metadata=metadata,
                )
            )
        return self._apply_score_threshold(reranked_results, knowledge_base.score_threshold)

    def _retrieve_lexical_results(
        self,
        *,
        user_id: str,
        knowledge_base: KnowledgeBase,
        query: str,
        top_k: int,
        filters: KnowledgeRetrievalFilter,
    ) -> list[RetrievalResult]:
        candidate_top_k = self._candidate_top_k(top_k=top_k, filters=filters)
        index_generation = self._active_generation(knowledge_base)
        hits = self._search_lexical_index(
            knowledge_base=knowledge_base,
            user_id=user_id,
            query=query,
            top_k=candidate_top_k,
            index_generation=index_generation,
        )
        if not hits:
            return []
        hit_vector_ids = [hit.vector_id for hit in hits]
        chunks = self.chunk_repo.list_by_vector_ids_and_knowledge_base(
            knowledge_base_id=knowledge_base.id,
            user_id=user_id,
            vector_ids=hit_vector_ids,
            index_generation=index_generation,
        )
        chunk_by_vector_id = {chunk.vector_id: chunk for chunk in chunks}
        max_score = max((hit.score for hit in hits), default=1.0) or 1.0
        scored: list[RetrievalResult] = []
        index_source = "persistent"
        for hit in hits:
            chunk = chunk_by_vector_id.get(hit.vector_id)
            if not chunk:
                continue
            metadata = json.loads(chunk.metadata_json or "{}")
            result = RetrievalResult(
                chunk=chunk,
                score=hit.score / max_score,
                rank_source="lexical",
                metadata={
                    **metadata,
                    "lexical_score": hit.score,
                    "normalized_score": hit.score / max_score,
                    "retrieval_mode": "lexical",
                    "lexical_index": index_source,
                },
            )
            if filters.enabled and not self._matches_filters(result, filters):
                continue
            scored.append(result)
            if len(scored) >= top_k:
                break
        return self._apply_score_threshold(scored, knowledge_base.score_threshold)

    def _search_lexical_index(
        self,
        *,
        knowledge_base: KnowledgeBase,
        user_id: str,
        query: str,
        top_k: int,
        index_generation: str,
    ) -> list[LexicalSearchHit]:
        try:
            return self.lexical_store.search(
                knowledge_base_id=knowledge_base.id,
                query=query,
                top_k=top_k,
                generation_id=index_generation,
            )
        except RuntimeError:
            chunks = self.chunk_repo.list_by_knowledge_base(
                knowledge_base.id,
                user_id,
                index_generation=index_generation,
            )
            if not chunks:
                return []
            self.lexical_store.rebuild(
                knowledge_base_id=knowledge_base.id,
                chunks=chunks,
                generation_id=index_generation,
            )
            return self.lexical_store.search(
                knowledge_base_id=knowledge_base.id,
                query=query,
                top_k=top_k,
                generation_id=index_generation,
            )

    @staticmethod
    def _active_generation(knowledge_base: KnowledgeBase) -> str:
        return knowledge_base.active_index_generation or "legacy"

    def _retrieve_lexical_results_in_memory(
        self,
        *,
        user_id: str,
        knowledge_base: KnowledgeBase,
        query: str,
        top_k: int,
        filters: KnowledgeRetrievalFilter,
    ) -> list[RetrievalResult]:
        """Kept for tests/comparison only; production path uses KnowledgeLexicalStore."""
        query_terms = KnowledgeLexicalStore.tokenize(query)
        if not query_terms:
            return []
        from collections import Counter
        import math

        query_counts = Counter(query_terms)
        chunks = self.chunk_repo.list_by_knowledge_base(
            knowledge_base.id,
            user_id,
            index_generation=self._active_generation(knowledge_base),
        )
        docs: list[tuple[object, dict, Counter[str]]] = []
        document_frequency: Counter[str] = Counter()
        total_length = 0
        for chunk in chunks:
            metadata = json.loads(chunk.metadata_json or "{}")
            probe = RetrievalResult(chunk=chunk, score=0, metadata=metadata)
            if filters.enabled and not self._matches_filters(probe, filters):
                continue
            terms = KnowledgeLexicalStore.tokenize(chunk.content)
            if not terms:
                continue
            counts = Counter(terms)
            docs.append((chunk, metadata, counts))
            total_length += sum(counts.values())
            for term in set(counts):
                document_frequency[term] += 1
        if not docs:
            return []

        avgdl = max(1.0, total_length / len(docs))
        scored: list[RetrievalResult] = []
        for chunk, metadata, counts in docs:
            score = self._bm25_score(
                query_counts=query_counts,
                doc_counts=counts,
                document_frequency=document_frequency,
                doc_count=len(docs),
                avgdl=avgdl,
            )
            if score <= 0:
                continue
            scored.append(
                RetrievalResult(
                    chunk=chunk,
                    score=score,
                    rank_source="lexical",
                    metadata={
                        **metadata,
                        "lexical_score": score,
                        "retrieval_mode": "lexical",
                    },
                )
            )
        scored.sort(key=lambda item: item.score, reverse=True)
        normalized = self._normalize_result_scores(scored[:top_k], score_key="lexical_score")
        return self._apply_score_threshold(normalized, knowledge_base.score_threshold)

    @staticmethod
    def _normalize_result_scores(results: list[RetrievalResult], *, score_key: str) -> list[RetrievalResult]:
        if not results:
            return []
        max_score = max(result.score for result in results) or 1.0
        normalized: list[RetrievalResult] = []
        for result in results:
            raw_score = float(result.score)
            score = raw_score / max_score if max_score > 0 else 0.0
            normalized.append(
                RetrievalResult(
                    chunk=result.chunk,
                    score=score,
                    rerank_score=result.rerank_score,
                    rank_source=result.rank_source,
                    metadata={
                        **result.metadata,
                        score_key: raw_score,
                        "normalized_score": score,
                    },
                )
            )
        return normalized

    @staticmethod
    def _bm25_score(
        *,
        query_counts: Counter[str],
        doc_counts: Counter[str],
        document_frequency: Counter[str],
        doc_count: int,
        avgdl: float,
    ) -> float:
        score = 0.0
        doc_len = max(1, sum(doc_counts.values()))
        for term, query_weight in query_counts.items():
            tf = doc_counts.get(term, 0)
            if tf <= 0:
                continue
            df = document_frequency.get(term, 0)
            if df <= 0:
                continue
            idf = math.log(1 + (doc_count - df + 0.5) / (df + 0.5))
            denom = tf + KnowledgeLexicalStore.BM25_K1 * (
                1 - KnowledgeLexicalStore.BM25_B + KnowledgeLexicalStore.BM25_B * doc_len / avgdl
            )
            score += float(query_weight) * idf * (tf * (KnowledgeLexicalStore.BM25_K1 + 1) / denom)
        return score

    @staticmethod
    def _fuse_rrf(
        *,
        vector_results: list[RetrievalResult],
        lexical_results: list[RetrievalResult],
        top_k: int,
        vector_error: str | None = None,
    ) -> list[RetrievalResult]:
        rrf_k = 60
        fused: dict[str, dict[str, object]] = {}

        def add_results(results: list[RetrievalResult], source: str) -> None:
            for rank, result in enumerate(results, start=1):
                entry = fused.setdefault(
                    result.chunk.id,
                    {
                        "result": result,
                        "rrf_score": 0.0,
                        "vector_rank": None,
                        "lexical_rank": None,
                        "vector_score": None,
                        "lexical_score": None,
                        "lexical_index": None,
                    },
                )
                entry["rrf_score"] = float(entry["rrf_score"]) + 1 / (rrf_k + rank)
                entry[f"{source}_rank"] = rank
                if source == "vector":
                    entry["vector_score"] = result.score
                else:
                    entry["lexical_score"] = result.metadata.get("lexical_score", result.score)
                    entry["lexical_index"] = result.metadata.get("lexical_index")

        add_results(vector_results, "vector")
        add_results(lexical_results, "lexical")
        ranked = sorted(fused.values(), key=lambda entry: float(entry["rrf_score"]), reverse=True)[:top_k]
        max_rrf = max((float(entry["rrf_score"]) for entry in ranked), default=1.0) or 1.0
        results: list[RetrievalResult] = []
        for entry in ranked:
            result = entry["result"]
            assert isinstance(result, RetrievalResult)
            rrf_score = float(entry["rrf_score"])
            score = rrf_score / max_rrf if max_rrf > 0 else 0.0
            metadata = {
                **result.metadata,
                "retrieval_mode": "hybrid",
                "rrf_score": rrf_score,
                "normalized_score": score,
                "vector_rank": entry["vector_rank"],
                "lexical_rank": entry["lexical_rank"],
                "vector_score": entry["vector_score"],
                "lexical_score": entry["lexical_score"],
                "lexical_index": entry["lexical_index"],
            }
            if vector_error:
                metadata["vector_error"] = vector_error
                metadata["hybrid_fallback"] = True
            results.append(
                RetrievalResult(
                    chunk=result.chunk,
                    score=score,
                    rank_source="hybrid_rrf",
                    metadata=metadata,
                )
            )
        return results

    @staticmethod
    def _candidate_top_k(*, top_k: int, filters: KnowledgeRetrievalFilter) -> int:
        if not filters.enabled:
            return top_k
        return min(max(top_k * 5, top_k + 20), 200)

    @staticmethod
    def _apply_filters(results: list[RetrievalResult], filters: KnowledgeRetrievalFilter) -> list[RetrievalResult]:
        return [result for result in results if KnowledgeRetrievalPipeline._matches_filters(result, filters)]

    @staticmethod
    def _matches_filters(result: RetrievalResult, filters: KnowledgeRetrievalFilter) -> bool:
        chunk = result.chunk
        metadata = result.metadata or {}
        if filters.document_ids and chunk.document_id not in filters.document_ids:
            return False
        if filters.file_types:
            file_type = KnowledgeRetrievalPipeline._normalize_file_type(
                str(metadata.get("file_type") or metadata.get("mime_type") or "")
            )
            if file_type not in filters.file_types:
                return False
        page_number = KnowledgeRetrievalPipeline._extract_page_number(metadata)
        if filters.page_start is not None and (page_number is None or page_number < filters.page_start):
            return False
        if filters.page_end is not None and (page_number is None or page_number > filters.page_end):
            return False
        if filters.section_query:
            section = str(metadata.get("section_title") or metadata.get("heading") or "").lower()
            if filters.section_query.lower() not in section:
                return False
        return True

    @staticmethod
    def _normalize_file_type(value: str) -> str:
        normalized = value.strip().lower()
        if normalized in {"application/pdf", "pdf"}:
            return "pdf"
        if normalized in {"text/markdown", "markdown", "md"}:
            return "markdown"
        if normalized in {"text/plain", "txt", "plain"}:
            return "text"
        if normalized in {"text/html", "html"}:
            return "html"
        return normalized

    @staticmethod
    def _extract_page_number(metadata: dict) -> int | None:
        for key in ("page_number", "page", "source_page"):
            value = metadata.get(key)
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.strip().isdigit():
                return int(value.strip())
        return None

    @staticmethod
    def _mark_rerank_fallback(results: list[RetrievalResult], reason: str) -> list[RetrievalResult]:
        marked: list[RetrievalResult] = []
        for result in results:
            metadata = {
                **result.metadata,
                "rerank_fallback": True,
                "rerank_error": reason,
            }
            if "vector_score" not in metadata and result.rank_source in {"vector", "vector_fallback"}:
                metadata["vector_score"] = result.score
            marked.append(
                RetrievalResult(
                    chunk=result.chunk,
                    score=result.score,
                    rerank_score=None,
                    rank_source="vector_fallback" if result.rank_source == "vector" else result.rank_source,
                    metadata=metadata,
                )
            )
        return marked

    @staticmethod
    def _apply_score_threshold(results: list[RetrievalResult], score_threshold: float) -> list[RetrievalResult]:
        if score_threshold <= 0:
            return results
        filtered: list[RetrievalResult] = []
        for result in results:
            score = result.rerank_score if result.rerank_score is not None else result.score
            if score >= score_threshold:
                filtered.append(result)
        return filtered

    @staticmethod
    def _validate_vectors(*, vectors: list[list[float]], expected_count: int, dimensions: int) -> None:
        if len(vectors) != expected_count:
            raise RuntimeError(f"Embedding 返回数量不一致：期望 {expected_count}，实际 {len(vectors)}。")
        for vector in vectors:
            if len(vector) != dimensions:
                raise RuntimeError(f"Embedding 维度不一致：知识库维度 {dimensions}，实际返回 {len(vector)}。")
