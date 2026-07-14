from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import tempfile
from collections import Counter, defaultdict
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
    metadata: dict | None = None


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


@dataclass(frozen=True)
class LexicalSearchHit:
    vector_id: int
    score: float


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

    def split_parent_child(
        self,
        *,
        markdown: str,
        parent_chunk_size: int,
        child_chunk_size: int,
        child_chunk_overlap: int,
    ) -> list[PreparedChunk]:
        normalized = self._normalize_markdown(markdown)
        if not normalized:
            return []
        parents = self.split(markdown=normalized, chunk_size=parent_chunk_size, chunk_overlap=0)
        children: list[PreparedChunk] = []
        for parent_index, parent in enumerate(parents):
            parent_children = self._window_chunk(
                parent.content,
                parent.source_start,
                child_chunk_size,
                child_chunk_overlap,
                len(children),
            )
            for child in parent_children:
                children.append(
                    PreparedChunk(
                        content=child.content,
                        chunk_index=len(children),
                        source_start=child.source_start,
                        source_end=child.source_end,
                        metadata={
                            "chunk_mode": "parent_child",
                            "retrieval_unit": "child",
                            "parent_index": parent_index,
                            "parent_content": parent.content,
                            "parent_source_start": parent.source_start,
                            "parent_source_end": parent.source_end,
                            "parent_char_count": len(parent.content),
                            "child_source_start": child.source_start,
                            "child_source_end": child.source_end,
                            "child_char_count": len(child.content),
                        },
                    )
                )
        return children

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
                        metadata=None,
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
        client = AsyncOpenAI(
            api_key=api_key or "sk-placeholder",
            base_url=base_url,
            timeout=settings.knowledge_model_request_timeout_seconds,
        )
        response = await client.embeddings.create(model=knowledge_base.embedding_model, input=texts)
        return [list(item.embedding) for item in response.data]

    async def _embed_ollama(self, *, user_id: str, knowledge_base: KnowledgeBase, texts: list[str]) -> list[list[float]]:
        if not self.setting_service:
            raise RuntimeError("知识库 Embedding 设置服务未初始化。")
        setting = self.setting_service.get_or_create_user_settings(user_id)
        base_url = (setting.knowledge_embedding_base_url or settings.ollama_base_url).rstrip("/")
        async with httpx.AsyncClient(timeout=settings.knowledge_model_request_timeout_seconds) as client:
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
        async with httpx.AsyncClient(timeout=settings.knowledge_model_request_timeout_seconds) as client:
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

    def index_path(self, knowledge_base_id: str, *, generation_id: str | None = None) -> Path:
        base_dir = self.index_root / knowledge_base_id
        if not generation_id or generation_id == "legacy":
            return base_dir / self.INDEX_FILE_NAME
        return base_dir / "generations" / generation_id / self.INDEX_FILE_NAME

    def rebuild(
        self,
        *,
        knowledge_base_id: str,
        chunks: list[KnowledgeChunk],
        vectors: list[list[float]],
        dimensions: int,
        generation_id: str | None = None,
    ) -> str:
        if len(chunks) != len(vectors):
            raise RuntimeError("Chunk 数量和向量数量不一致。")
        index_path = self.index_path(knowledge_base_id, generation_id=generation_id)
        target_dir = index_path.parent
        target_dir.mkdir(parents=True, exist_ok=True)
        index = faiss.IndexIDMap2(faiss.IndexFlatIP(dimensions))
        if vectors:
            matrix = self._normalize(np.array(vectors, dtype="float32"))
            ids = np.array([chunk.vector_id for chunk in chunks], dtype="int64")
            index.add_with_ids(matrix, ids)
        # 不能直接覆盖正式索引：进程中断或磁盘写失败会同时毁掉旧版本。
        # 临时文件与目标文件放在同一目录，os.replace 才能提供同文件系统内的原子发布。
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=target_dir,
                prefix=f".{self.INDEX_FILE_NAME}.",
                suffix=".tmp",
                delete=False,
            ) as temp_file:
                temp_path = Path(temp_file.name)
            faiss.write_index(index, str(temp_path))
            os.replace(temp_path, index_path)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
        return str(index_path)

    def search(
        self,
        *,
        knowledge_base_id: str,
        query_vector: list[float],
        top_k: int,
        generation_id: str | None = None,
    ) -> list[tuple[int, float]]:
        index_path = self.index_path(knowledge_base_id, generation_id=generation_id)
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


class KnowledgeLexicalStore:
    INDEX_FILE_NAME = "lexical_index.json"
    VERSION = 1
    BM25_K1 = 1.5
    BM25_B = 0.75

    def __init__(self, index_root: str | None = None):
        self.index_root = Path(index_root or settings.knowledge_index_dir)

    def index_path(self, knowledge_base_id: str, *, generation_id: str | None = None) -> Path:
        base_dir = self.index_root / knowledge_base_id
        if not generation_id or generation_id == "legacy":
            return base_dir / self.INDEX_FILE_NAME
        return base_dir / "generations" / generation_id / self.INDEX_FILE_NAME

    def exists(self, *, knowledge_base_id: str, generation_id: str | None = None) -> bool:
        return self.index_path(knowledge_base_id, generation_id=generation_id).exists()

    def rebuild(
        self,
        *,
        knowledge_base_id: str,
        chunks: list[KnowledgeChunk],
        generation_id: str | None = None,
    ) -> str:
        index_path = self.index_path(knowledge_base_id, generation_id=generation_id)
        target_dir = index_path.parent
        target_dir.mkdir(parents=True, exist_ok=True)

        postings: dict[str, list[list[int]]] = defaultdict(list)
        document_frequency: Counter[str] = Counter()
        document_lengths: dict[str, int] = {}
        chunk_refs: dict[str, dict[str, int | str]] = {}
        total_length = 0

        for chunk in chunks:
            terms = self.tokenize(chunk.content)
            counts = Counter(terms)
            doc_len = sum(counts.values())
            if doc_len <= 0:
                continue
            vector_key = str(chunk.vector_id)
            document_lengths[vector_key] = doc_len
            chunk_refs[vector_key] = {
                "chunk_id": chunk.id,
                "document_id": chunk.document_id,
                "chunk_index": chunk.chunk_index,
            }
            total_length += doc_len
            for term, count in counts.items():
                postings[term].append([chunk.vector_id, int(count)])
                document_frequency[term] += 1

        doc_count = len(document_lengths)
        payload = {
            "version": self.VERSION,
            "knowledge_base_id": knowledge_base_id,
            "doc_count": doc_count,
            "avgdl": (total_length / doc_count) if doc_count else 1.0,
            "document_lengths": document_lengths,
            "document_frequency": dict(document_frequency),
            "chunk_refs": chunk_refs,
            "postings": dict(postings),
        }
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=target_dir,
                prefix=f".{self.INDEX_FILE_NAME}.",
                suffix=".tmp",
                mode="w",
                encoding="utf-8",
                delete=False,
            ) as temp_file:
                temp_path = Path(temp_file.name)
                json.dump(payload, temp_file, ensure_ascii=False)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            os.replace(temp_path, index_path)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
        return str(index_path)

    def search(
        self,
        *,
        knowledge_base_id: str,
        query: str,
        top_k: int,
        generation_id: str | None = None,
    ) -> list[LexicalSearchHit]:
        payload = self._load(knowledge_base_id=knowledge_base_id, generation_id=generation_id)
        query_terms = self.tokenize(query)
        if not query_terms:
            return []
        query_counts = Counter(query_terms)
        doc_count = int(payload.get("doc_count") or 0)
        if doc_count <= 0:
            return []
        avgdl = max(1.0, float(payload.get("avgdl") or 1.0))
        document_lengths = payload.get("document_lengths")
        document_frequency = payload.get("document_frequency")
        postings = payload.get("postings")
        if not isinstance(document_lengths, dict) or not isinstance(document_frequency, dict) or not isinstance(postings, dict):
            raise RuntimeError("知识库 BM25 索引文件格式不合法，请重建索引。")

        scores: dict[int, float] = defaultdict(float)
        for term, query_weight in query_counts.items():
            term_postings = postings.get(term)
            if not isinstance(term_postings, list):
                continue
            df = int(document_frequency.get(term) or 0)
            if df <= 0:
                continue
            idf = math.log(1 + (doc_count - df + 0.5) / (df + 0.5))
            for posting in term_postings:
                if not isinstance(posting, list) or len(posting) != 2:
                    continue
                vector_id, tf = posting
                if not isinstance(vector_id, int) or not isinstance(tf, int) or tf <= 0:
                    continue
                doc_len = max(1, int(document_lengths.get(str(vector_id)) or 0))
                denom = tf + self.BM25_K1 * (1 - self.BM25_B + self.BM25_B * doc_len / avgdl)
                scores[vector_id] += float(query_weight) * idf * (tf * (self.BM25_K1 + 1) / denom)

        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        return [LexicalSearchHit(vector_id=vector_id, score=score) for vector_id, score in ranked[: max(1, top_k)] if score > 0]

    def _load(self, *, knowledge_base_id: str, generation_id: str | None = None) -> dict:
        index_path = self.index_path(knowledge_base_id, generation_id=generation_id)
        if not index_path.exists():
            raise RuntimeError("知识库尚未生成 BM25 索引，请先索引文档。")
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RuntimeError("知识库 BM25 索引文件损坏，请重建索引。") from exc
        if not isinstance(payload, dict) or payload.get("version") != self.VERSION:
            raise RuntimeError("知识库 BM25 索引版本不兼容，请重建索引。")
        return payload

    @staticmethod
    def tokenize(text: str) -> list[str]:
        normalized = text.lower()
        latin_terms = re.findall(r"[a-z0-9][a-z0-9_\-]{1,}|[a-z0-9]", normalized)
        cjk_chars = re.findall(r"[\u4e00-\u9fff]", normalized)
        cjk_bigrams = [f"{cjk_chars[index]}{cjk_chars[index + 1]}" for index in range(len(cjk_chars) - 1)]
        return latin_terms + cjk_chars + cjk_bigrams


class KnowledgeIndexService:
    EMBEDDING_VERSION = "l2-normalized-v1"

    def __init__(
        self,
        *,
        chunk_repo: KnowledgeChunkRepository,
        document_repo: KnowledgeDocumentRepository,
        setting_service: SettingService,
        embedding_service: KnowledgeEmbeddingService | None = None,
        rerank_service: KnowledgeRerankService | None = None,
        faiss_store: KnowledgeFaissStore | None = None,
        lexical_store: KnowledgeLexicalStore | None = None,
    ):
        self.chunk_repo = chunk_repo
        self.document_repo = document_repo
        self.setting_service = setting_service
        self.embedding_service = embedding_service or KnowledgeEmbeddingService(setting_service)
        self.rerank_service = rerank_service or KnowledgeRerankService(setting_service)
        self.faiss_store = faiss_store or KnowledgeFaissStore()
        self._faiss_store_was_injected = faiss_store is not None
        self.lexical_store = lexical_store or KnowledgeLexicalStore()
        self.chunker = KnowledgeChunker()

    def backfill_active_generation_embeddings(
        self,
        *,
        user_id: str,
        knowledge_base: KnowledgeBase,
    ) -> int:
        """Persist missing/stale vectors without rebuilding or activating an index generation."""
        index_generation = knowledge_base.active_index_generation or "legacy"
        chunks = self.chunk_repo.list_by_knowledge_base(
            knowledge_base.id,
            user_id,
            index_generation=index_generation,
        )
        pending_chunks = [
            chunk
            for chunk in chunks
            if not self._has_reusable_embedding(chunk=chunk, knowledge_base=knowledge_base)
        ]
        if not pending_chunks:
            return 0

        # 外部请求和向量校验先于数据库修改；失败时不留下部分回填。
        generated_vectors = asyncio.run(
            self.embedding_service.embed_texts(
                user_id=user_id,
                knowledge_base=knowledge_base,
                texts=[chunk.content for chunk in pending_chunks],
            )
        )
        self._validate_vectors(
            vectors=generated_vectors,
            expected_count=len(pending_chunks),
            dimensions=knowledge_base.embedding_dimensions,
        )
        for chunk, vector in zip(pending_chunks, generated_vectors, strict=True):
            chunk.embedding = self._normalize_vector(vector)
            chunk.embedding_provider = knowledge_base.embedding_provider
            chunk.embedding_model = knowledge_base.embedding_model
            chunk.embedding_dimensions = knowledge_base.embedding_dimensions
            chunk.embedding_version = self.EMBEDDING_VERSION
        self.chunk_repo.save_embeddings(pending_chunks)
        return len(pending_chunks)

    def import_active_generation_embeddings_from_faiss(
        self,
        *,
        user_id: str,
        knowledge_base: KnowledgeBase,
    ) -> int:
        """Migrate the normalized vectors already serving legacy FAISS queries into pgvector."""
        index_generation = knowledge_base.active_index_generation or "legacy"
        chunks = self.chunk_repo.list_by_knowledge_base(
            knowledge_base.id,
            user_id,
            index_generation=index_generation,
        )
        pending_chunks = [
            chunk
            for chunk in chunks
            if not self._has_reusable_embedding(chunk=chunk, knowledge_base=knowledge_base)
        ]
        if not pending_chunks:
            return 0

        index_path = self.faiss_store.index_path(
            knowledge_base.id,
            generation_id=index_generation,
        )
        if not index_path.exists():
            raise RuntimeError(f"FAISS 索引不存在：{index_path}")
        index = faiss.read_index(str(index_path))
        if index.d != knowledge_base.embedding_dimensions:
            raise RuntimeError(
                f"FAISS 维度与知识库配置不一致："
                f"index={index.d}, knowledge_base={knowledge_base.embedding_dimensions}"
            )
        if not hasattr(index, "id_map"):
            raise RuntimeError("FAISS 索引缺少 vector_id 映射，不能安全迁移。")
        index_ids = [int(item) for item in faiss.vector_to_array(index.id_map).tolist()]
        if len(index_ids) != len(set(index_ids)):
            raise RuntimeError("FAISS 索引包含重复 vector_id，已拒绝迁移。")
        chunk_ids = {chunk.vector_id for chunk in chunks}
        if set(index_ids) != chunk_ids:
            missing_in_faiss = sorted(chunk_ids - set(index_ids))[:10]
            missing_in_database = sorted(set(index_ids) - chunk_ids)[:10]
            raise RuntimeError(
                "FAISS 与数据库 vector_id 集合不一致，已拒绝迁移："
                f"missing_in_faiss={missing_in_faiss}, missing_in_database={missing_in_database}"
            )

        vectors_by_id = {
            vector_id: index.reconstruct(vector_id).astype("float32").tolist()
            for vector_id in index_ids
        }
        pending_vectors = [vectors_by_id[chunk.vector_id] for chunk in pending_chunks]
        self._validate_vectors(
            vectors=pending_vectors,
            expected_count=len(pending_chunks),
            dimensions=knowledge_base.embedding_dimensions,
        )
        for chunk, vector in zip(pending_chunks, pending_vectors, strict=True):
            chunk.embedding = vector
            chunk.embedding_provider = knowledge_base.embedding_provider
            chunk.embedding_model = knowledge_base.embedding_model
            chunk.embedding_dimensions = knowledge_base.embedding_dimensions
            chunk.embedding_version = self.EMBEDDING_VERSION
        self.chunk_repo.save_embeddings(pending_chunks)
        return len(pending_chunks)

    def index_document(self, *, user_id: str, knowledge_base: KnowledgeBase, document: KnowledgeDocument) -> IndexResult:
        """Build and publish one document's chunks as part of a knowledge-base-wide index.

        The current storage format has one FAISS file and one BM25 file per knowledge base, so indexing one
        document must rebuild both files from all chunks. Keep fallible external work before database mutation,
        and keep the vector_id -> embedding association explicit when publishing the rebuilt files.
        """
        if document.parse_status != "parsed" or not document.parsed_markdown_path:
            raise RuntimeError("文档尚未解析，不能生成索引。")
        markdown = KnowledgeParserService().read_markdown(markdown_path=document.parsed_markdown_path, user_id=user_id)
        if knowledge_base.chunk_mode == "parent_child":
            parent_chunk_size = knowledge_base.parent_chunk_size or max(knowledge_base.chunk_size, 2000)
            child_chunk_size = knowledge_base.child_chunk_size or max(100, min(knowledge_base.chunk_size, 500))
            child_chunk_overlap = knowledge_base.child_chunk_overlap or min(80, child_chunk_size - 1)
            prepared_chunks = self.chunker.split_parent_child(
                markdown=markdown,
                parent_chunk_size=parent_chunk_size,
                child_chunk_size=child_chunk_size,
                child_chunk_overlap=child_chunk_overlap,
            )
        else:
            prepared_chunks = self.chunker.split(
                markdown=markdown,
                chunk_size=knowledge_base.chunk_size,
                chunk_overlap=knowledge_base.chunk_overlap,
            )
        if not prepared_chunks:
            raise RuntimeError("文档解析结果为空，不能生成索引。")

        index_generation = knowledge_base.active_index_generation or "legacy"
        vector_start = self.chunk_repo.max_vector_id(
            knowledge_base.id,
            user_id,
            index_generation=index_generation,
        ) + 1
        chunks = [
            KnowledgeChunk(
                user_id=user_id,
                knowledge_base_id=knowledge_base.id,
                document_id=document.id,
                index_generation=index_generation,
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
                        "mime_type": document.mime_type,
                        "file_type": self._normalize_file_type(document.file_name, document.mime_type),
                        "document_id": document.id,
                        "chunk_index": chunk.chunk_index,
                        "source_start": chunk.source_start,
                        "source_end": chunk.source_end,
                        "document_version": document.document_version,
                        "parser_provider": document.parser_provider,
                        **(chunk.metadata or {}),
                    },
                    ensure_ascii=False,
                ),
            )
            for offset, chunk in enumerate(prepared_chunks)
        ]
        existing_chunks = self.chunk_repo.list_by_knowledge_base(
            knowledge_base.id,
            user_id,
            index_generation=index_generation,
        )
        retained_chunks = [chunk for chunk in existing_chunks if chunk.document_id != document.id]
        chunks_to_publish = [*retained_chunks, *chunks]

        # 目标文档会生成新 Chunk 行，但内容未变的 Chunk 仍可在删除旧行前按 hash 复用向量。
        # 这里只使用签名与当前知识库完整匹配的缓存，换模型或维度后会自动 miss。
        reusable_vector_by_hash = {
            chunk.content_hash: list(chunk.embedding or [])
            for chunk in existing_chunks
            if self._has_reusable_embedding(chunk=chunk, knowledge_base=knowledge_base)
        }
        vector_by_id: dict[int, list[float]] = {}
        chunks_missing_embedding: list[KnowledgeChunk] = []
        for chunk in chunks_to_publish:
            if self._has_reusable_embedding(chunk=chunk, knowledge_base=knowledge_base):
                vector_by_id[chunk.vector_id] = list(chunk.embedding or [])
                continue
            reusable_vector = reusable_vector_by_hash.get(chunk.content_hash)
            if reusable_vector is not None:
                vector_by_id[chunk.vector_id] = reusable_vector
                continue
            chunks_missing_embedding.append(chunk)

        # Embedding 是最昂贵、最容易因网络或供应商失败的步骤。必须在替换数据库 chunks 之前完成，
        # 否则重索引失败会留下“新数据库 chunks + 旧 FAISS/BM25 文件”的不可查询状态。
        if chunks_missing_embedding:
            generated_vectors = asyncio.run(
                self.embedding_service.embed_texts(
                    user_id=user_id,
                    knowledge_base=knowledge_base,
                    texts=[chunk.content for chunk in chunks_missing_embedding],
                )
            )
            self._validate_vectors(
                vectors=generated_vectors,
                expected_count=len(chunks_missing_embedding),
                dimensions=knowledge_base.embedding_dimensions,
            )
            for chunk, vector in zip(chunks_missing_embedding, generated_vectors, strict=True):
                vector_by_id[chunk.vector_id] = vector

        vectors_to_publish = [vector_by_id[chunk.vector_id] for chunk in chunks_to_publish]
        # 缓存命中也经过同一道校验，防止历史脏数据进入新索引。
        self._validate_vectors(
            vectors=vectors_to_publish,
            expected_count=len(chunks_to_publish),
            dimensions=knowledge_base.embedding_dimensions,
        )

        # 向量与生成它的模型签名一起持久化。未来只有签名完整匹配时才能复用，
        # 避免知识库更换 provider/model 后把旧向量错当成当前向量。
        for chunk, vector in zip(chunks_to_publish, vectors_to_publish, strict=True):
            chunk.embedding = self._normalize_vector(vector)
            chunk.embedding_provider = knowledge_base.embedding_provider
            chunk.embedding_model = knowledge_base.embedding_model
            chunk.embedding_dimensions = knowledge_base.embedding_dimensions
            chunk.embedding_version = self.EMBEDDING_VERSION
        saved_chunks = self.chunk_repo.replace_document_chunks(
            document.id,
            user_id,
            chunks,
            index_generation=index_generation,
        )
        all_chunks = self.chunk_repo.list_by_knowledge_base(
            knowledge_base.id,
            user_id,
            index_generation=index_generation,
        )
        all_vectors = [vector_by_id[chunk.vector_id] for chunk in all_chunks]
        index_path = self.faiss_store.rebuild(
            knowledge_base_id=knowledge_base.id,
            chunks=all_chunks,
            vectors=all_vectors,
            dimensions=knowledge_base.embedding_dimensions,
            generation_id=index_generation,
        )
        self.lexical_store.rebuild(
            knowledge_base_id=knowledge_base.id,
            chunks=all_chunks,
            generation_id=index_generation,
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
        return self._build_retrieval_pipeline().retrieve(
            user_id=user_id,
            knowledge_base=knowledge_base,
            query=query,
            top_k=top_k,
        )

    async def retrieve_async(
        self,
        *,
        user_id: str,
        knowledge_base: KnowledgeBase,
        query: str,
        top_k: int,
    ) -> list[RetrievalResult]:
        return await self._build_retrieval_pipeline().retrieve_async(
            user_id=user_id,
            knowledge_base=knowledge_base,
            query=query,
            top_k=top_k,
        )

    def _build_retrieval_pipeline(self):
        from app.services.knowledge_retrieval_pipeline import KnowledgeRetrievalPipeline

        return KnowledgeRetrievalPipeline(
            chunk_repo=self.chunk_repo,
            setting_service=self.setting_service,
            embedding_service=self.embedding_service,
            rerank_service=self.rerank_service,
            faiss_store=self.faiss_store if self._faiss_store_was_injected else None,
            lexical_store=self.lexical_store,
        )

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
            if any(not isinstance(value, (int, float)) or not math.isfinite(value) for value in vector):
                raise RuntimeError("Embedding 返回了非有限数值，已拒绝写入向量索引。")

    @staticmethod
    def _has_reusable_embedding(*, chunk: KnowledgeChunk, knowledge_base: KnowledgeBase) -> bool:
        embedding = chunk.embedding
        return bool(
            embedding is not None
            and chunk.embedding_provider == knowledge_base.embedding_provider
            and chunk.embedding_model == knowledge_base.embedding_model
            and chunk.embedding_dimensions == knowledge_base.embedding_dimensions
            and chunk.embedding_version == KnowledgeIndexService.EMBEDDING_VERSION
            and len(embedding) == knowledge_base.embedding_dimensions
        )

    @staticmethod
    def _normalize_vector(vector: list[float]) -> list[float]:
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0:
            return list(vector)
        return [value / norm for value in vector]

    @staticmethod
    def _normalize_file_type(file_name: str, mime_type: str | None) -> str:
        mime = (mime_type or "").strip().lower()
        suffix = Path(file_name).suffix.lower().lstrip(".")
        if mime == "application/pdf" or suffix == "pdf":
            return "pdf"
        if mime == "text/markdown" or suffix in {"md", "markdown"}:
            return "markdown"
        if mime == "text/plain" or suffix == "txt":
            return "text"
        if mime == "text/html" or suffix in {"html", "htm"}:
            return "html"
        return suffix or mime or "unknown"
