from __future__ import annotations

import asyncio
import json

from app.models.knowledge import KnowledgeBase
from app.repositories.knowledge_repo import KnowledgeChunkRepository
from app.services.knowledge_index_service import (
    KnowledgeEmbeddingService,
    KnowledgeFaissStore,
    KnowledgeRerankService,
    RetrievalResult,
)
from app.services.setting_service import SettingService


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
    ) -> None:
        self.chunk_repo = chunk_repo
        self.setting_service = setting_service
        self.embedding_service = embedding_service or KnowledgeEmbeddingService(setting_service)
        self.rerank_service = rerank_service or KnowledgeRerankService(setting_service)
        self.faiss_store = faiss_store or KnowledgeFaissStore()

    def retrieve(
        self,
        *,
        user_id: str,
        knowledge_base: KnowledgeBase,
        query: str,
        top_k: int,
    ) -> list[RetrievalResult]:
        return asyncio.run(
            self.retrieve_async(
                user_id=user_id,
                knowledge_base=knowledge_base,
                query=query,
                top_k=top_k,
            )
        )

    async def retrieve_async(
        self,
        *,
        user_id: str,
        knowledge_base: KnowledgeBase,
        query: str,
        top_k: int,
    ) -> list[RetrievalResult]:
        query_vector = (
            await self.embedding_service.embed_texts(
                user_id=user_id,
                knowledge_base=knowledge_base,
                texts=[query],
            )
        )[0]
        self._validate_vectors(vectors=[query_vector], expected_count=1, dimensions=knowledge_base.embedding_dimensions)
        hits = self.faiss_store.search(knowledge_base_id=knowledge_base.id, query_vector=query_vector, top_k=top_k)
        if not hits:
            return []

        score_by_id = {vector_id: score for vector_id, score in hits}
        chunks = self.chunk_repo.list_by_knowledge_base(knowledge_base.id, user_id)
        chunk_by_vector_id = {chunk.vector_id: chunk for chunk in chunks}
        results: list[RetrievalResult] = []
        for vector_id, score in hits:
            chunk = chunk_by_vector_id.get(vector_id)
            if not chunk:
                continue
            metadata = json.loads(chunk.metadata_json or "{}")
            results.append(RetrievalResult(chunk=chunk, score=score_by_id[vector_id], metadata=metadata))

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
            reranked_results.append(
                RetrievalResult(
                    chunk=result.chunk,
                    score=result.score,
                    rerank_score=rerank_score,
                    rank_source="rerank",
                    metadata={
                        **result.metadata,
                        "vector_score": result.score,
                        "rerank_model": knowledge_base.rerank_model,
                    },
                )
            )
        return self._apply_score_threshold(reranked_results, knowledge_base.score_threshold)

    @staticmethod
    def _mark_rerank_fallback(results: list[RetrievalResult], reason: str) -> list[RetrievalResult]:
        return [
            RetrievalResult(
                chunk=result.chunk,
                score=result.score,
                rerank_score=None,
                rank_source="vector_fallback",
                metadata={
                    **result.metadata,
                    "vector_score": result.score,
                    "rerank_fallback": True,
                    "rerank_error": reason,
                },
            )
            for result in results
        ]

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
