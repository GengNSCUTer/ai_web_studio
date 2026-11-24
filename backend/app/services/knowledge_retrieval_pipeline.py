from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field, replace

from app.models.knowledge import KnowledgeBase
from app.repositories.knowledge_repo import KnowledgeChunkRepository
from app.services.knowledge_index_service import (
    KnowledgeEmbeddingService,
    LexicalSearchHit,
    LexicalChunkSnapshot,
    KnowledgeLexicalStore,
    KnowledgeRerankService,
    RetrievalResult,
)
from app.services.knowledge_error import (
    KnowledgeRetrievalUnavailableError,
    classify_knowledge_error,
)
from app.services.knowledge_vector_search import (
    KnowledgeVectorSearch,
    PgvectorKnowledgeVectorSearch,
)
from app.services.setting_service import SettingService
from app.services.knowledge_query_expansion import KnowledgeQueryExpansionService
from app.services.knowledge_search_text import retrieval_text
from app.core.config import settings


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

    RRF_K = 60
    # 扩大的是精排输入，不增加最终注入段落；限制两路并集，避免请求成本无界增长。
    MAX_RERANK_CANDIDATES = 100

    def __init__(
        self,
        *,
        chunk_repo: KnowledgeChunkRepository,
        setting_service: SettingService,
        embedding_service: KnowledgeEmbeddingService | None = None,
        rerank_service: KnowledgeRerankService | None = None,
        vector_search: KnowledgeVectorSearch | None = None,
        lexical_store: KnowledgeLexicalStore | None = None,
        enhancement: str | None = None,
    ) -> None:
        self.chunk_repo = chunk_repo
        self.setting_service = setting_service
        self.embedding_service = embedding_service or KnowledgeEmbeddingService(setting_service)
        self.rerank_service = rerank_service or KnowledgeRerankService(setting_service)
        self.vector_search = vector_search or PgvectorKnowledgeVectorSearch(chunk_repo)
        self.enhancement = enhancement if enhancement is not None else getattr(settings, "knowledge_retrieval_enhancement", "none")
        self.lexical_store = lexical_store or KnowledgeLexicalStore(contextual=self.enhancement in {"context", "both"})
        self.query_expansion = KnowledgeQueryExpansionService(setting_service)

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
        self, *, user_id: str, knowledge_base: KnowledgeBase, query: str, top_k: int,
        filters: KnowledgeRetrievalFilter | None = None,
    ) -> list[RetrievalResult]:
        results = await self._retrieve_async(user_id=user_id, knowledge_base=knowledge_base,
                                             query=query, top_k=top_k, filters=filters)
        pending = {}
        for result in results:
            index = result.metadata.get("parent_chunk_index")
            if result.metadata.get("chunk_mode") == "parent_child" and isinstance(index, int) and not result.metadata.get("parent_content"):
                chunk = result.chunk
                pending[(chunk.knowledge_base_id, chunk.document_id, chunk.index_generation, index)] = True
        if not pending:
            return results
        anchors = self.chunk_repo.parent_anchors(user_id=user_id, keys=list(pending))
        parents = {(c.knowledge_base_id, c.document_id, c.index_generation, c.chunk_index):
                   json.loads(c.metadata_json or "{}").get("parent_content") for c in anchors}
        hydrated = []
        for result in results:
            c = result.chunk
            key = (c.knowledge_base_id, c.document_id, c.index_generation, result.metadata.get("parent_chunk_index"))
            content = parents.get(key)
            hydrated.append(replace(result, metadata={**result.metadata, "parent_content": content})
                            if isinstance(content, str) and content.strip() else result)
        return hydrated

    async def _retrieve_async(
        self,
        *,
        user_id: str,
        knowledge_base: KnowledgeBase,
        query: str,
        top_k: int,
        filters: KnowledgeRetrievalFilter | None = None,
    ) -> list[RetrievalResult]:
        filters = filters or KnowledgeRetrievalFilter()
        if self.enhancement in {"query", "both"}:
            query, _ = await self.query_expansion.expand(user_id=user_id, query=query)
        retrieval_mode = (knowledge_base.retrieval_mode or "vector").strip().lower()
        rerank_active = bool(knowledge_base.rerank_enabled and knowledge_base.rerank_top_n > 0)
        channel_top_k = top_k
        if rerank_active:
            channel_top_k = min(top_k * 2, self.MAX_RERANK_CANDIDATES // 2)
        index_generation = self._active_generation(knowledge_base)
        if retrieval_mode == "lexical":
            results = await self._retrieve_lexical_results(
                user_id=user_id,
                knowledge_base=knowledge_base,
                query=query,
                top_k=channel_top_k,
                filters=filters,
                index_generation=index_generation,
            )
            return await self._rerank_if_needed(
                user_id=user_id,
                knowledge_base=knowledge_base,
                query=query,
                results=results,
                result_limit=top_k,
            )
        if retrieval_mode == "hybrid":
            candidate_top_k = self._candidate_top_k(top_k=channel_top_k, filters=filters)
            async def channel(operation):
                try:
                    return await operation, None
                except Exception as exc:
                    # 两路独立降级；取消属于控制信号，不转换为“检索失败”。
                    return [], classify_knowledge_error(exc)

            tasks = [asyncio.create_task(channel(operation)) for operation in (
                self._retrieve_vector_results(user_id=user_id, knowledge_base=knowledge_base,
                    query=query, top_k=candidate_top_k, filters=filters, index_generation=index_generation),
                self._retrieve_lexical_results(user_id=user_id, knowledge_base=knowledge_base,
                    query=query, top_k=candidate_top_k, filters=filters, index_generation=index_generation),
            )]
            try:
                (vector_results, vector_error_code), (lexical_results, lexical_error_code) = await asyncio.gather(*tasks)
            finally:
                # 一路被取消或调用方退出时，回收另一路协程，避免继续查询或回填结果。
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
            results = self._fuse_rrf(
                vector_results=vector_results,
                lexical_results=lexical_results,
                top_k=self.MAX_RERANK_CANDIDATES if rerank_active else top_k,
                vector_error_code=vector_error_code,
                lexical_error_code=lexical_error_code,
            )
            if vector_error_code and lexical_error_code and not results:
                # Do not turn a two-channel outage into a misleading "no
                # relevant document" result.  Context/evaluation callers can
                # classify this stable error and close the request safely.
                raise KnowledgeRetrievalUnavailableError()
            return await self._rerank_if_needed(
                user_id=user_id,
                knowledge_base=knowledge_base,
                query=query,
                results=results,
                result_limit=top_k,
            )
        results = await self._retrieve_vector_results(
            user_id=user_id,
            knowledge_base=knowledge_base,
            query=query,
            top_k=channel_top_k,
            filters=filters,
            index_generation=index_generation,
        )
        return await self._rerank_if_needed(
            user_id=user_id,
            knowledge_base=knowledge_base,
            query=query,
            results=results,
            result_limit=top_k,
        )

    async def _retrieve_vector_results(
        self,
        *,
        user_id: str,
        knowledge_base: KnowledgeBase,
        query: str,
        top_k: int,
        filters: KnowledgeRetrievalFilter,
        index_generation: str | None = None,
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
        index_generation = index_generation or self._active_generation(knowledge_base)
        hits = self.vector_search.search(
            user_id=user_id,
            knowledge_base=knowledge_base,
            index_generation=index_generation,
            query_vector=query_vector,
            top_k=candidate_top_k,
        )
        if not hits:
            return []
        results = [
            RetrievalResult(
                chunk=hit.chunk,
                score=hit.score,
                metadata=json.loads(hit.chunk.metadata_json or "{}"),
            )
            for hit in hits
        ]
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
        result_limit: int | None = None,
    ) -> list[RetrievalResult]:
        if not results:
            return []
        # 精排不可用时只返回调用者原本申请的数量，不将扩大后的候选池泄漏到上下文。
        fallback_results = results[:result_limit] if result_limit is not None else results
        if not knowledge_base.rerank_enabled or len(results) <= 1:
            return self._apply_score_threshold(fallback_results, knowledge_base.score_threshold)
        if knowledge_base.rerank_top_n <= 0:
            return self._apply_score_threshold(fallback_results, knowledge_base.score_threshold)

        rerank_top_n = min(knowledge_base.rerank_top_n, len(results))
        if result_limit is not None:
            rerank_top_n = min(rerank_top_n, result_limit)
        try:
            reranked = await self.rerank_service.rerank(
                user_id=user_id,
                knowledge_base=knowledge_base,
                query=query,
                documents=[retrieval_text(result.chunk, result.metadata)
                           if self.enhancement in {"context", "both"} else result.chunk.content
                           for result in results],
                top_n=rerank_top_n,
            )
        except Exception as exc:
            return self._apply_score_threshold(
                self._mark_rerank_fallback(fallback_results, classify_knowledge_error(exc)),
                knowledge_base.score_threshold,
            )
        if not reranked:
            return self._apply_score_threshold(
                self._mark_rerank_fallback(fallback_results, "empty_response"),
                knowledge_base.score_threshold,
            )

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
                "rerank_candidate_count": len(results),
                "rerank_output_limit": rerank_top_n,
                "rerank_score_policy": "rank_only",
                "retrieval_enhancement": self.enhancement,
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
        # 精排相关性分数不是向量余弦相似度，也不是 RRF 归一化分数。
        # 未经专门校准不能复用召回阈值；成功精排仅按模型排名和 top_n 收口。
        return reranked_results[:rerank_top_n]

    async def _retrieve_lexical_results(
        self,
        *,
        user_id: str,
        knowledge_base: KnowledgeBase,
        query: str,
        top_k: int,
        filters: KnowledgeRetrievalFilter,
        index_generation: str | None = None,
    ) -> list[RetrievalResult]:
        candidate_top_k = self._candidate_top_k(top_k=top_k, filters=filters)
        index_generation = index_generation or self._active_generation(knowledge_base)
        hits = await self._search_lexical_index(
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

    async def _search_lexical_index(
        self,
        *,
        knowledge_base: KnowledgeBase,
        user_id: str,
        query: str,
        top_k: int,
        index_generation: str,
    ) -> list[LexicalSearchHit]:
        # 在线程启动前冻结主键；线程闭包只读取普通索引对象与字符串，不访问ORM。
        knowledge_base_id = knowledge_base.id
        store = self.lexical_store
        def search():
            if store.contextual:
                primary = KnowledgeLexicalStore(index_root=str(store.index_root)).index_path(
                    knowledge_base_id, generation_id=index_generation)
                contextual = store.index_path(knowledge_base_id, generation_id=index_generation)
                if primary.exists() and contextual.exists() and primary.stat().st_mtime_ns > contextual.stat().st_mtime_ns:
                    raise RuntimeError("结构索引落后于当前发布索引，需重建")
            return store.search(knowledge_base_id=knowledge_base_id, query=query,
                top_k=top_k, generation_id=index_generation)
        try:
            return await asyncio.to_thread(search)
        except RuntimeError:
            # 缺索引时仍在拥有Session的线程读DB，随后仅把标量快照交给重建线程。
            chunks = self.chunk_repo.list_by_knowledge_base(
                knowledge_base_id, user_id, index_generation=index_generation)
            if not chunks:
                return []
            snapshots = [LexicalChunkSnapshot(id=chunk.id, document_id=chunk.document_id,
                chunk_index=chunk.chunk_index, vector_id=chunk.vector_id,
                content=chunk.content, metadata_json=chunk.metadata_json) for chunk in chunks]
            await asyncio.to_thread(store.rebuild, knowledge_base_id=knowledge_base_id,
                chunks=snapshots, generation_id=index_generation)
            return await asyncio.to_thread(search)

    @staticmethod
    def _active_generation(knowledge_base: KnowledgeBase) -> str:
        return knowledge_base.active_index_generation or "legacy"

    @staticmethod
    def _fuse_rrf(
        *,
        vector_results: list[RetrievalResult],
        lexical_results: list[RetrievalResult],
        top_k: int,
        vector_error_code: str | None = None,
        lexical_error_code: str | None = None,
    ) -> list[RetrievalResult]:
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
                entry["rrf_score"] = float(entry["rrf_score"]) + 1 / (
                    KnowledgeRetrievalPipeline.RRF_K + rank
                )
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
            if vector_error_code or lexical_error_code:
                metadata["hybrid_fallback"] = True
                metadata["hybrid_fallback_reason"] = (
                    "both_channels_failed"
                    if vector_error_code and lexical_error_code
                    else "vector_unavailable"
                    if vector_error_code
                    else "lexical_unavailable"
                )
                metadata["both_channels_failed"] = bool(vector_error_code and lexical_error_code)
                metadata["vector_error_code"] = vector_error_code
                metadata["lexical_error_code"] = lexical_error_code
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
                "rerank_fallback_reason": reason,
                "rerank_error_code": reason,
                # Kept as a compatibility alias for older consumers; the
                # value is a stable code, never the provider exception text.
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
