from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path

import faiss
import httpx
import numpy as np
from openai import AsyncOpenAI

from app.core.config import settings
from app.models.knowledge import KnowledgeBase, KnowledgeChunk, KnowledgeDocument
from app.repositories.knowledge_repo import KnowledgeChunkRepository, KnowledgeDocumentRepository
from app.services.knowledge_parser_service import KnowledgeParserService
from app.services.setting_service import SettingService


@dataclass(frozen=True)
class PreparedChunk:
    content: str
    chunk_index: int
    source_start: int
    source_end: int


@dataclass(frozen=True)
class IndexResult:
    chunk_count: int
    index_path: str


@dataclass(frozen=True)
class RetrievalResult:
    chunk: KnowledgeChunk
    score: float
    metadata: dict
    rerank_score: float | None = None
    rank_source: str = "vector"


class KnowledgeChunker:
    def split(self, *, markdown: str, chunk_size: int, chunk_overlap: int) -> list[PreparedChunk]:
        normalized = self._normalize_markdown(markdown)
        if not normalized:
            return []
        paragraphs = self._split_paragraphs(normalized)
        chunks: list[PreparedChunk] = []
        buffer = ""
        buffer_start = 0
        cursor = 0

        for paragraph in paragraphs:
            paragraph_start = normalized.find(paragraph, cursor)
            if paragraph_start < 0:
                paragraph_start = cursor
            paragraph_end = paragraph_start + len(paragraph)
            cursor = paragraph_end
            candidate = paragraph if not buffer else f"{buffer}\n\n{paragraph}"
            if len(candidate) <= chunk_size:
                if not buffer:
                    buffer_start = paragraph_start
                buffer = candidate
                continue
            if buffer:
                chunks.extend(self._window_chunk(buffer, buffer_start, chunk_size, chunk_overlap, len(chunks)))
            buffer = paragraph
            buffer_start = paragraph_start
            if len(buffer) > chunk_size:
                chunks.extend(self._window_chunk(buffer, buffer_start, chunk_size, chunk_overlap, len(chunks)))
                buffer = ""

        if buffer:
            chunks.extend(self._window_chunk(buffer, buffer_start, chunk_size, chunk_overlap, len(chunks)))
        return chunks

    @staticmethod
    def _normalize_markdown(markdown: str) -> str:
        normalized = markdown.replace("\r\n", "\n").replace("\r", "\n")
        return re.sub(r"\n{3,}", "\n\n", normalized).strip()

    @staticmethod
    def _split_paragraphs(markdown: str) -> list[str]:
        parts = [part.strip() for part in re.split(r"\n\s*\n", markdown) if part.strip()]
        if parts:
            return parts
        return [markdown.strip()]

    @staticmethod
    def _window_chunk(text: str, start_offset: int, chunk_size: int, chunk_overlap: int, base_index: int) -> list[PreparedChunk]:
        if not text.strip():
            return []
        chunks: list[PreparedChunk] = []
        step = max(1, chunk_size - min(chunk_overlap, chunk_size - 1))
        local_start = 0
        while local_start < len(text):
            local_end = min(len(text), local_start + chunk_size)
            content = text[local_start:local_end].strip()
            if content:
                chunks.append(
                    PreparedChunk(
                        content=content,
                        chunk_index=base_index + len(chunks),
                        source_start=start_offset + local_start,
                        source_end=start_offset + local_end,
                    )
                )
            if local_end >= len(text):
                break
            local_start += step
        return chunks


class KnowledgeEmbeddingService:
    def __init__(self, setting_service: SettingService | None = None):
        self.setting_service = setting_service

    async def embed_texts(self, *, user_id: str, knowledge_base: KnowledgeBase, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        provider = knowledge_base.embedding_provider
        if provider == "ollama":
            return await self._embed_ollama(user_id=user_id, knowledge_base=knowledge_base, texts=texts)
        return await self._embed_openai_compatible(user_id=user_id, knowledge_base=knowledge_base, texts=texts)

    async def _embed_openai_compatible(self, *, user_id: str, knowledge_base: KnowledgeBase, texts: list[str]) -> list[list[float]]:
        if not self.setting_service:
            raise RuntimeError("知识库 Embedding 设置服务未初始化。")
        setting = self.setting_service.get_or_create_user_settings(user_id)
        base_url = setting.knowledge_embedding_base_url
        api_key = self.setting_service.resolve_knowledge_model_api_key(user_id, "embedding")
        client = AsyncOpenAI(api_key=api_key or "sk-placeholder", base_url=base_url)
        response = await client.embeddings.create(model=knowledge_base.embedding_model, input=texts)
        return [list(item.embedding) for item in response.data]

    async def _embed_ollama(self, *, user_id: str, knowledge_base: KnowledgeBase, texts: list[str]) -> list[list[float]]:
        if not self.setting_service:
            raise RuntimeError("知识库 Embedding 设置服务未初始化。")
        setting = self.setting_service.get_or_create_user_settings(user_id)
        base_url = (setting.knowledge_embedding_base_url or settings.ollama_base_url).rstrip("/")
        async with httpx.AsyncClient(timeout=settings.ollama_request_timeout_seconds) as client:
            tasks = [
                client.post(
                    f"{base_url}/api/embed",
                    json={"model": knowledge_base.embedding_model, "input": text},
                )
                for text in texts
            ]
            responses = await asyncio.gather(*tasks)
        vectors: list[list[float]] = []
        for response in responses:
            response.raise_for_status()
            payload = response.json()
            embeddings = payload.get("embeddings")
            if isinstance(embeddings, list) and embeddings:
                vectors.append(list(embeddings[0]))
                continue
            embedding = payload.get("embedding")
            if isinstance(embedding, list):
                vectors.append(list(embedding))
                continue
            raise RuntimeError("Ollama embedding 响应缺少向量。")
        return vectors


class KnowledgeRerankService:
    def __init__(self, setting_service: SettingService | None = None):
        self.setting_service = setting_service

    async def rerank(
        self,
        *,
        user_id: str,
        knowledge_base: KnowledgeBase,
        query: str,
        documents: list[str],
        top_n: int,
    ) -> list[tuple[int, float]]:
        if not documents:
            return []
        if knowledge_base.rerank_provider == "ollama":
            raise RuntimeError("当前暂不支持 Ollama Rerank，请关闭 Rerank 或选择 OpenAI-compatible Provider。")
        return await self._rerank_openai_compatible(
            user_id=user_id,
            knowledge_base=knowledge_base,
            query=query,
            documents=documents,
            top_n=top_n,
        )

    async def _rerank_openai_compatible(
        self,
        *,
        user_id: str,
        knowledge_base: KnowledgeBase,
        query: str,
        documents: list[str],
        top_n: int,
    ) -> list[tuple[int, float]]:
        if not self.setting_service:
            raise RuntimeError("知识库 Rerank 设置服务未初始化。")
        setting = self.setting_service.get_or_create_user_settings(user_id)
        base_url = (setting.knowledge_rerank_base_url or setting.knowledge_embedding_base_url).rstrip("/")
        api_key = self.setting_service.resolve_knowledge_model_api_key(user_id, "rerank")
        if not api_key:
            raise RuntimeError("知识库 Rerank API Key 未配置。")
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        async with httpx.AsyncClient(timeout=settings.ollama_request_timeout_seconds) as client:
            response = await client.post(
                f"{base_url}/rerank",
                headers=headers,
                json={
                    "model": knowledge_base.rerank_model,
                    "query": query,
                    "documents": documents,
                    "top_n": min(top_n, len(documents)),
                    "return_documents": False,
                },
            )
        response.raise_for_status()
        payload = response.json()
        results = payload.get("results")
        if not isinstance(results, list):
            raise RuntimeError("Rerank 响应缺少 results 字段。")
        ranked: list[tuple[int, float]] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            index = item.get("index")
            score = item.get("relevance_score")
            if isinstance(index, int) and isinstance(score, (int, float)):
                ranked.append((index, float(score)))
        return ranked


class KnowledgeFaissStore:
    INDEX_FILE_NAME = "index.faiss"

    def __init__(self, index_root: str | None = None):
        self.index_root = Path(index_root or settings.knowledge_index_dir)

    def index_path(self, knowledge_base_id: str) -> Path:
        return self.index_root / knowledge_base_id / self.INDEX_FILE_NAME

    def rebuild(self, *, knowledge_base_id: str, chunks: list[KnowledgeChunk], vectors: list[list[float]], dimensions: int) -> str:
        if len(chunks) != len(vectors):
            raise RuntimeError("Chunk 数量和向量数量不一致。")
        target_dir = self.index_root / knowledge_base_id
        target_dir.mkdir(parents=True, exist_ok=True)
        index_path = target_dir / self.INDEX_FILE_NAME
        index = faiss.IndexIDMap2(faiss.IndexFlatIP(dimensions))
        if vectors:
            matrix = self._normalize(np.array(vectors, dtype="float32"))
            ids = np.array([chunk.vector_id for chunk in chunks], dtype="int64")
            index.add_with_ids(matrix, ids)
        faiss.write_index(index, str(index_path))
        return str(index_path)

    def search(self, *, knowledge_base_id: str, query_vector: list[float], top_k: int) -> list[tuple[int, float]]:
        index_path = self.index_path(knowledge_base_id)
        if not index_path.exists():
            raise RuntimeError("知识库尚未生成向量索引，请先索引文档。")
        index = faiss.read_index(str(index_path))
        if index.ntotal == 0:
            return []
        matrix = self._normalize(np.array([query_vector], dtype="float32"))
        scores, ids = index.search(matrix, min(top_k, int(index.ntotal)))
        results: list[tuple[int, float]] = []
        for vector_id, score in zip(ids[0].tolist(), scores[0].tolist(), strict=False):
            if vector_id < 0:
                continue
            results.append((int(vector_id), float(score)))
        return results

    @staticmethod
    def _normalize(matrix: np.ndarray) -> np.ndarray:
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1
        return matrix / norms


class KnowledgeIndexService:
    def __init__(
        self,
        *,
        chunk_repo: KnowledgeChunkRepository,
        document_repo: KnowledgeDocumentRepository,
        setting_service: SettingService,
        embedding_service: KnowledgeEmbeddingService | None = None,
        rerank_service: KnowledgeRerankService | None = None,
        faiss_store: KnowledgeFaissStore | None = None,
    ):
        self.chunk_repo = chunk_repo
        self.document_repo = document_repo
        self.setting_service = setting_service
        self.embedding_service = embedding_service or KnowledgeEmbeddingService(setting_service)
        self.rerank_service = rerank_service or KnowledgeRerankService(setting_service)
        self.faiss_store = faiss_store or KnowledgeFaissStore()
        self.chunker = KnowledgeChunker()

    def index_document(self, *, user_id: str, knowledge_base: KnowledgeBase, document: KnowledgeDocument) -> IndexResult:
        if document.parse_status != "parsed" or not document.parsed_markdown_path:
            raise RuntimeError("文档尚未解析，不能生成索引。")
        markdown = KnowledgeParserService().read_markdown(markdown_path=document.parsed_markdown_path, user_id=user_id)
        prepared_chunks = self.chunker.split(
            markdown=markdown,
            chunk_size=knowledge_base.chunk_size,
            chunk_overlap=knowledge_base.chunk_overlap,
        )
        if not prepared_chunks:
            raise RuntimeError("文档解析结果为空，不能生成索引。")

        vector_start = self.chunk_repo.max_vector_id(knowledge_base.id, user_id) + 1
        chunks = [
            KnowledgeChunk(
                user_id=user_id,
                knowledge_base_id=knowledge_base.id,
                document_id=document.id,
                chunk_index=chunk.chunk_index,
                vector_id=vector_start + offset,
                content=chunk.content,
                content_hash=hashlib.sha256(chunk.content.encode("utf-8")).hexdigest(),
                char_count=len(chunk.content),
                token_estimate=max(1, len(chunk.content) // 4),
                source_start=chunk.source_start,
                source_end=chunk.source_end,
                metadata_json=json.dumps(
                    {
                        "file_name": document.file_name,
                        "document_version": document.document_version,
                        "parser_provider": document.parser_provider,
                    },
                    ensure_ascii=False,
                ),
            )
            for offset, chunk in enumerate(prepared_chunks)
        ]
        saved_chunks = self.chunk_repo.replace_document_chunks(document.id, user_id, chunks)
        all_chunks = self.chunk_repo.list_by_knowledge_base(knowledge_base.id, user_id)
        all_vectors = asyncio.run(
            self.embedding_service.embed_texts(
                user_id=user_id,
                knowledge_base=knowledge_base,
                texts=[chunk.content for chunk in all_chunks],
            )
        )
        self._validate_vectors(
            vectors=all_vectors,
            expected_count=len(all_chunks),
            dimensions=knowledge_base.embedding_dimensions,
        )
        index_path = self.faiss_store.rebuild(
            knowledge_base_id=knowledge_base.id,
            chunks=all_chunks,
            vectors=all_vectors,
            dimensions=knowledge_base.embedding_dimensions,
        )
        document.index_status = "indexed"
        document.error_message = None
        self.document_repo.save(document)
        return IndexResult(chunk_count=len(saved_chunks), index_path=index_path)

    def retrieve(
        self,
        *,
        user_id: str,
        knowledge_base: KnowledgeBase,
        query: str,
        top_k: int,
    ) -> list[RetrievalResult]:
        query_vector = asyncio.run(
            self.embedding_service.embed_texts(
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
            reranked = asyncio.run(
                self.rerank_service.rerank(
                    user_id=user_id,
                    knowledge_base=knowledge_base,
                    query=query,
                    documents=[result.chunk.content for result in results],
                    top_n=rerank_top_n,
                )
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
