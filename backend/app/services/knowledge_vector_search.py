from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from app.models.knowledge import KnowledgeBase, KnowledgeChunk
from app.repositories.knowledge_repo import KnowledgeChunkRepository


CURRENT_EMBEDDING_VERSION = "l2-normalized-v1"


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
            embedding_version=CURRENT_EMBEDDING_VERSION,
            top_k=top_k,
        )
        return [KnowledgeVectorSearchHit(chunk=chunk, score=score) for chunk, score in rows]
