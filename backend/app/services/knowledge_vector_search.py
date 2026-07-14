from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.models.knowledge import KnowledgeBase, KnowledgeChunk
from app.repositories.knowledge_repo import KnowledgeChunkRepository


@dataclass(frozen=True)
class KnowledgeVectorSearchHit:
    chunk: KnowledgeChunk
    score: float


class KnowledgeVectorSearch(Protocol):
    def search(
        self,
        *,
        user_id: str,
        knowledge_base: KnowledgeBase,
        index_generation: str,
        query_vector: list[float],
        top_k: int,
    ) -> list[KnowledgeVectorSearchHit]: ...


class PgvectorKnowledgeVectorSearch:
    """Production vector search backed by PostgreSQL/pgvector."""

    EMBEDDING_VERSION = "l2-normalized-v1"

    def __init__(self, chunk_repo: KnowledgeChunkRepository):
        self.chunk_repo = chunk_repo

    def search(
        self,
        *,
        user_id: str,
        knowledge_base: KnowledgeBase,
        index_generation: str,
        query_vector: list[float],
        top_k: int,
    ) -> list[KnowledgeVectorSearchHit]:
        rows = self.chunk_repo.search_by_cosine_distance(
            knowledge_base_id=knowledge_base.id,
            user_id=user_id,
            index_generation=index_generation,
            query_vector=query_vector,
            embedding_provider=knowledge_base.embedding_provider,
            embedding_model=knowledge_base.embedding_model,
            embedding_dimensions=knowledge_base.embedding_dimensions,
            embedding_version=self.EMBEDDING_VERSION,
            top_k=top_k,
        )
        return [KnowledgeVectorSearchHit(chunk=chunk, score=score) for chunk, score in rows]


class LegacyFaissVectorSearchAdapter:
    """Temporary adapter for legacy SQLite tests that explicitly inject FAISS."""

    def __init__(self, *, chunk_repo: KnowledgeChunkRepository, faiss_store):  # noqa: ANN001
        self.chunk_repo = chunk_repo
        self.faiss_store = faiss_store

    def search(
        self,
        *,
        user_id: str,
        knowledge_base: KnowledgeBase,
        index_generation: str,
        query_vector: list[float],
        top_k: int,
    ) -> list[KnowledgeVectorSearchHit]:
        hits = self.faiss_store.search(
            knowledge_base_id=knowledge_base.id,
            query_vector=query_vector,
            top_k=top_k,
            generation_id=index_generation,
        )
        if not hits:
            return []
        chunks = self.chunk_repo.list_by_vector_ids_and_knowledge_base(
            knowledge_base_id=knowledge_base.id,
            user_id=user_id,
            vector_ids=[vector_id for vector_id, _ in hits],
            index_generation=index_generation,
        )
        chunk_by_vector_id = {chunk.vector_id: chunk for chunk in chunks}
        return [
            KnowledgeVectorSearchHit(chunk=chunk_by_vector_id[vector_id], score=score)
            for vector_id, score in hits
            if vector_id in chunk_by_vector_id
        ]
