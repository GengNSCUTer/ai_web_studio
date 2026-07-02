from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.repositories.knowledge_repo import (
    KnowledgeBaseRepository,
    KnowledgeChunkRepository,
    KnowledgeDocumentRepository,
    KnowledgeRetrievalLogRepository,
)
from app.repositories.setting_repo import UserSettingRepository
from app.services.knowledge_index_service import RetrievalResult
from app.services.knowledge_retrieval_pipeline import KnowledgeRetrievalPipeline
from app.services.setting_service import SettingService
from app.services.tools.schemas import ExternalSource


@dataclass(frozen=True)
class KnowledgeContextResult:
    context_text: str | None
    sources: list[ExternalSource] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)
    retrieval_log_id: str | None = None
    retrieval_log_ids: list[str] = field(default_factory=list)


class KnowledgeContextService:
    """Retrieves indexed knowledge chunks and formats them for chat context."""

    def __init__(self, *, db: Session, user_id: str, index_service: object | None = None) -> None:
        self.db = db
        self.user_id = user_id
        self.base_repo = KnowledgeBaseRepository(db)
        self.document_repo = KnowledgeDocumentRepository(db)
        self.chunk_repo = KnowledgeChunkRepository(db)
        self.retrieval_log_repo = KnowledgeRetrievalLogRepository(db)
        self.setting_service = SettingService(UserSettingRepository(db))
        self.index_service = index_service

    async def build_context(
        self,
        *,
        knowledge_base_id: str | None,
        knowledge_base_ids: list[str] | None = None,
        query: str,
    ) -> KnowledgeContextResult:
        resolved_ids = self._normalize_knowledge_base_ids(knowledge_base_id, knowledge_base_ids)
        if not resolved_ids:
            return self._empty(enabled=False)
        if len(resolved_ids) == 1:
            return await self._build_single_context(knowledge_base_id=resolved_ids[0], query=query)

        if not query.strip():
            return self._empty(
                enabled=True,
                knowledge_base_ids=resolved_ids,
                notices=["当前问题为空，已跳过知识库检索。"],
            )

        started_at = time.monotonic()
        partials = await asyncio.gather(
            *[self._build_single_context(knowledge_base_id=base_id, query=query) for base_id in resolved_ids],
        )
        latency_ms = int((time.monotonic() - started_at) * 1000)
        notices = [notice for partial in partials for notice in partial.notices]
        all_sources = [source for partial in partials for source in partial.sources]
        ranked_sources = sorted(
            all_sources,
            key=lambda source: float(source.score or 0),
            reverse=True,
        )
        all_results = self._relabel_sources(ranked_sources)
        max_context_chars = self._multi_context_char_budget(resolved_ids)
        context_text = self._format_multi_context(results=all_results, max_chars=max_context_chars)
        injected_sources = all_results[: len(self._extract_context_labels(context_text))]
        retrieval_log_ids = [partial.retrieval_log_id for partial in partials if partial.retrieval_log_id]
        diagnostics = self._merge_multi_diagnostics(
            partials=partials,
            knowledge_base_ids=resolved_ids,
            latency_ms=latency_ms,
            context_text=context_text,
            injected_count=len(injected_sources),
            retrieval_log_ids=retrieval_log_ids,
        )
        return KnowledgeContextResult(
            context_text=context_text,
            sources=injected_sources,
            notices=notices,
            diagnostics=diagnostics,
            details={
                "knowledge_sources": [source.to_public_dict() for source in injected_sources],
                "knowledge_retrieval_log_ids": retrieval_log_ids,
                "knowledge_base_ids": resolved_ids,
            },
            retrieval_log_id=retrieval_log_ids[0] if retrieval_log_ids else None,
            retrieval_log_ids=retrieval_log_ids,
        )

    async def _build_single_context(
        self,
        *,
        knowledge_base_id: str,
        query: str,
    ) -> KnowledgeContextResult:
        knowledge_base = self.base_repo.get_by_user(knowledge_base_id, self.user_id)
        if not knowledge_base:
            return self._empty(
                enabled=True,
                error=1,
                notices=["所选知识库不存在或无权访问，已跳过知识库检索。"],
            )

        if not query.strip():
            return self._empty(
                enabled=True,
                knowledge_base_id=knowledge_base.id,
                knowledge_base_name=knowledge_base.name,
                notices=["当前问题为空，已跳过知识库检索。"],
            )

        total_chunks = self.chunk_repo.count_by_knowledge_base(knowledge_base.id, self.user_id)
        if total_chunks <= 0:
            return self._empty(
                enabled=True,
                knowledge_base_id=knowledge_base.id,
                knowledge_base_name=knowledge_base.name,
                notices=[f"知识库「{knowledge_base.name}」尚未生成索引，已跳过检索。"],
            )

        started_at = time.monotonic()
        try:
            index_service = self.index_service or KnowledgeRetrievalPipeline(
                chunk_repo=self.chunk_repo,
                setting_service=self.setting_service,
            )
            results = await asyncio.wait_for(
                index_service.retrieve_async(
                    user_id=self.user_id,
                    knowledge_base=knowledge_base,
                    query=query,
                    top_k=knowledge_base.retrieval_top_k,
                ),
                timeout=settings.knowledge_context_timeout_seconds,
            )
        except asyncio.TimeoutError:
            return self._empty(
                enabled=True,
                error=1,
                knowledge_base_id=knowledge_base.id,
                knowledge_base_name=knowledge_base.name,
                notices=[
                    f"知识库「{knowledge_base.name}」检索超过 {settings.knowledge_context_timeout_seconds} 秒，已跳过本轮知识库上下文。"
                ],
                latency_ms=int((time.monotonic() - started_at) * 1000),
            )
        except Exception as exc:
            return self._empty(
                enabled=True,
                error=1,
                knowledge_base_id=knowledge_base.id,
                knowledge_base_name=knowledge_base.name,
                notices=[f"知识库「{knowledge_base.name}」检索失败：{exc}"],
                latency_ms=int((time.monotonic() - started_at) * 1000),
            )
        latency_ms = int((time.monotonic() - started_at) * 1000)

        selected = results[: max(1, knowledge_base.max_context_chunks)]
        context_text = self._format_context(
            knowledge_base_name=knowledge_base.name,
            results=selected,
            max_chars=knowledge_base.max_context_chars,
        )
        base_diagnostics = {
            "knowledge_retrieval_enabled": 1,
            "knowledge_base_id": knowledge_base.id,
            "knowledge_base_name": knowledge_base.name,
            "knowledge_chunks_total": total_chunks,
            "knowledge_chunks_retrieved": len(results),
            "knowledge_chunks_injected": len(selected) if context_text else 0,
            "knowledge_context_chars": len(context_text or ""),
            "knowledge_rerank_enabled": int(bool(knowledge_base.rerank_enabled)),
            "knowledge_rerank_used": int(any(result.rerank_score is not None for result in selected)),
            "knowledge_retrieval_latency_ms": latency_ms,
        }
        retrieval_log = self.retrieval_log_repo.create(
            user_id=self.user_id,
            knowledge_base_id=knowledge_base.id,
            query=query,
            retrieval_mode=knowledge_base.retrieval_mode,
            top_k=knowledge_base.retrieval_top_k,
            rerank_enabled=bool(knowledge_base.rerank_enabled),
            rerank_model=knowledge_base.rerank_model if knowledge_base.rerank_enabled else None,
            candidates=self._serialize_results(results),
            selected=self._serialize_results(selected),
            diagnostics=base_diagnostics,
            sources=[],
            status="success",
            elapsed_ms=latency_ms,
        )
        sources = self._build_sources(
            knowledge_base_id=knowledge_base.id,
            knowledge_base_name=knowledge_base.name,
            results=selected,
            retrieval_log_id=retrieval_log.id,
        )
        return KnowledgeContextResult(
            context_text=context_text,
            sources=sources,
            notices=[],
            diagnostics={**base_diagnostics, "knowledge_retrieval_log_id": retrieval_log.id},
            details={
                "knowledge_sources": [source.to_public_dict() for source in sources],
                "knowledge_retrieval_log_id": retrieval_log.id,
                "knowledge_retrieval_log_ids": [retrieval_log.id],
            },
            retrieval_log_id=retrieval_log.id,
            retrieval_log_ids=[retrieval_log.id],
        )

    @staticmethod
    def _empty(
        *,
        enabled: bool,
        error: int = 0,
        knowledge_base_id: str | None = None,
        knowledge_base_ids: list[str] | None = None,
        knowledge_base_name: str | None = None,
        notices: list[str] | None = None,
        latency_ms: int = 0,
    ) -> KnowledgeContextResult:
        resolved_ids = knowledge_base_ids or ([knowledge_base_id] if knowledge_base_id else [])
        return KnowledgeContextResult(
            context_text=None,
            sources=[],
            notices=notices or [],
            diagnostics={
                "knowledge_retrieval_enabled": int(enabled),
                "knowledge_base_id": knowledge_base_id or "",
                "knowledge_base_ids": ",".join(resolved_ids),
                "knowledge_base_count": len(resolved_ids),
                "knowledge_base_name": knowledge_base_name or "",
                "knowledge_chunks_total": 0,
                "knowledge_chunks_retrieved": 0,
                "knowledge_chunks_injected": 0,
                "knowledge_context_chars": 0,
                "knowledge_rerank_enabled": 0,
                "knowledge_rerank_used": 0,
                "knowledge_retrieval_error": error,
                "knowledge_retrieval_latency_ms": latency_ms,
            },
            details={"knowledge_sources": []},
        )

    @staticmethod
    def _normalize_knowledge_base_ids(
        knowledge_base_id: str | None,
        knowledge_base_ids: list[str] | None,
    ) -> list[str]:
        values = [item.strip() for item in (knowledge_base_ids or []) if item and item.strip()]
        if knowledge_base_id and knowledge_base_id.strip():
            values.insert(0, knowledge_base_id.strip())
        return list(dict.fromkeys(values))[:10]

    @staticmethod
    def _format_context(*, knowledge_base_name: str, results: list[RetrievalResult], max_chars: int) -> str | None:
        if not results:
            return None
        lines = [f"知识库：{knowledge_base_name}", "请优先依据以下知识库片段回答；若片段不足以回答，请明确说明不确定。"]
        used_chars = sum(len(line) for line in lines)
        for index, result in enumerate(results, start=1):
            file_name = str(result.metadata.get("file_name") or "unknown")
            score = result.rerank_score if result.rerank_score is not None else result.score
            expansion = "；parent-child：已扩展" if KnowledgeContextService._is_parent_child_result(result) else ""
            header = f"\n[KB{index}] 文件：{file_name}；chunk：{result.chunk.chunk_index}；score：{score:.4f}{expansion}"
            content = KnowledgeContextService._result_display_text(result).strip()
            remaining = max_chars - used_chars - len(header)
            if remaining <= 120:
                break
            if len(content) > remaining:
                content = content[:remaining].rstrip() + "..."
            lines.append(f"{header}\n{content}")
            used_chars += len(header) + len(content)
        return "\n".join(lines).strip() if len(lines) > 2 else None

    @staticmethod
    def _format_multi_context(*, results: list[ExternalSource], max_chars: int) -> str | None:
        if not results:
            return None
        lines = ["知识库：多知识库检索", "请优先依据以下知识库片段回答；若片段不足以回答，请明确说明不确定。"]
        used_chars = sum(len(line) for line in lines)
        for index, source in enumerate(results, start=1):
            metadata = source.metadata or {}
            knowledge_base_name = str(metadata.get("knowledge_base_name") or "unknown")
            file_name = str(metadata.get("file_name") or "unknown")
            chunk_index = str(metadata.get("chunk_index") or "")
            score = float(source.score or 0)
            header = f"\n[KB{index}] 知识库：{knowledge_base_name}；文件：{file_name}；chunk：{chunk_index}；score：{score:.4f}"
            content = source.display_text.strip()
            remaining = max_chars - used_chars - len(header)
            if remaining <= 120:
                break
            if len(content) > remaining:
                content = content[:remaining].rstrip() + "..."
            lines.append(f"{header}\n{content}")
            used_chars += len(header) + len(content)
        return "\n".join(lines).strip() if len(lines) > 2 else None

    @staticmethod
    def _extract_context_labels(context_text: str | None) -> list[str]:
        if not context_text:
            return []
        return [line for line in context_text.splitlines() if line.startswith("[KB")]

    def _multi_context_char_budget(self, knowledge_base_ids: list[str]) -> int:
        budgets: list[int] = []
        for base_id in knowledge_base_ids:
            knowledge_base = self.base_repo.get_by_user(base_id, self.user_id)
            if knowledge_base:
                budgets.append(max(1000, int(knowledge_base.max_context_chars or 12000)))
        if not budgets:
            return 12000
        return min(sum(budgets), 30000)

    @staticmethod
    def _merge_multi_diagnostics(
        *,
        partials: list[KnowledgeContextResult],
        knowledge_base_ids: list[str],
        latency_ms: int,
        context_text: str | None,
        injected_count: int,
        retrieval_log_ids: list[str],
    ) -> dict[str, Any]:
        total_chunks = sum(int(partial.diagnostics.get("knowledge_chunks_total", 0) or 0) for partial in partials)
        retrieved = sum(int(partial.diagnostics.get("knowledge_chunks_retrieved", 0) or 0) for partial in partials)
        errors = sum(int(partial.diagnostics.get("knowledge_retrieval_error", 0) or 0) for partial in partials)
        rerank_enabled = int(any(int(partial.diagnostics.get("knowledge_rerank_enabled", 0) or 0) for partial in partials))
        rerank_used = int(any(int(partial.diagnostics.get("knowledge_rerank_used", 0) or 0) for partial in partials))
        names = [
            str(partial.diagnostics.get("knowledge_base_name") or "")
            for partial in partials
            if str(partial.diagnostics.get("knowledge_base_name") or "").strip()
        ]
        return {
            "knowledge_retrieval_enabled": 1,
            "knowledge_base_id": knowledge_base_ids[0] if knowledge_base_ids else "",
            "knowledge_base_ids": ",".join(knowledge_base_ids),
            "knowledge_base_count": len(knowledge_base_ids),
            "knowledge_base_name": "、".join(names) if names else "多知识库",
            "knowledge_chunks_total": total_chunks,
            "knowledge_chunks_retrieved": retrieved,
            "knowledge_chunks_injected": injected_count,
            "knowledge_context_chars": len(context_text or ""),
            "knowledge_rerank_enabled": rerank_enabled,
            "knowledge_rerank_used": rerank_used,
            "knowledge_retrieval_error": int(errors > 0),
            "knowledge_retrieval_latency_ms": latency_ms,
            "knowledge_retrieval_log_id": retrieval_log_ids[0] if retrieval_log_ids else "",
            "knowledge_retrieval_log_ids": ",".join(retrieval_log_ids),
        }

    @staticmethod
    def _relabel_sources(sources: list[ExternalSource]) -> list[ExternalSource]:
        relabeled: list[ExternalSource] = []
        for index, source in enumerate(sources, start=1):
            metadata = {**(source.metadata or {}), "citation_label": f"[KB{index}]"}
            relabeled.append(
                ExternalSource(
                    source_type=source.source_type,
                    provider=source.provider,
                    title=source.title,
                    url=source.url,
                    display_text=source.display_text,
                    rank=index,
                    score=source.score,
                    used_in_prompt=source.used_in_prompt,
                    citation_label=f"[KB{index}]",
                    metadata=json.loads(json.dumps(metadata, ensure_ascii=False, default=str)),
                )
            )
        return relabeled

    @staticmethod
    def _serialize_results(results: list[RetrievalResult]) -> list[dict[str, Any]]:
        serialized: list[dict[str, Any]] = []
        for rank, result in enumerate(results, start=1):
            serialized.append(
                {
                    "rank": rank,
                    "chunk_id": result.chunk.id,
                    "knowledge_base_id": result.chunk.knowledge_base_id,
                    "document_id": result.chunk.document_id,
                    "file_name": result.metadata.get("file_name") or "unknown",
                    "chunk_index": result.chunk.chunk_index,
                    "vector_id": result.chunk.vector_id,
                    "score": result.rerank_score if result.rerank_score is not None else result.score,
                    "vector_score": result.metadata.get("vector_score")
                    if isinstance(result.metadata.get("vector_score"), (int, float))
                    else result.score,
                    "rerank_score": result.rerank_score,
                    "rank_source": result.rank_source,
                    "char_count": result.chunk.char_count,
                    "token_estimate": result.chunk.token_estimate,
                    "source_start": result.chunk.source_start,
                    "source_end": result.chunk.source_end,
                    "preview": KnowledgeContextService._result_display_text(result)[:1200],
                    "metadata": result.metadata,
                }
            )
        return serialized

    @staticmethod
    def _build_sources(
        *,
        knowledge_base_id: str,
        knowledge_base_name: str,
        results: list[RetrievalResult],
        retrieval_log_id: str | None = None,
    ) -> list[ExternalSource]:
        sources: list[ExternalSource] = []
        for index, result in enumerate(results, start=1):
            file_name = str(result.metadata.get("file_name") or "unknown")
            score = result.rerank_score if result.rerank_score is not None else result.score
            metadata = {
                **result.metadata,
                "knowledge_base_id": knowledge_base_id,
                "knowledge_base_name": knowledge_base_name,
                "document_id": result.chunk.document_id,
                "chunk_id": result.chunk.id,
                "chunk_index": result.chunk.chunk_index,
                "vector_score": result.metadata.get("vector_score")
                if isinstance(result.metadata.get("vector_score"), (int, float))
                else result.score,
                "rerank_score": result.rerank_score,
                "rank_source": result.rank_source,
                "source_start": result.chunk.source_start,
                "source_end": result.chunk.source_end,
                "retrieval_log_id": retrieval_log_id,
                "tool": "knowledge_retrieval",
            }
            display_text = KnowledgeContextService._result_display_text(result)
            sources.append(
                ExternalSource(
                    source_type="knowledge",
                    provider="knowledge_base",
                    title=f"{knowledge_base_name} / {file_name}",
                    display_text=display_text[:1200],
                    rank=index,
                    score=score,
                    used_in_prompt=True,
                    citation_label=f"[KB{index}]",
                    metadata=json.loads(json.dumps(metadata, ensure_ascii=False, default=str)),
                )
            )
        return sources

    @staticmethod
    def _is_parent_child_result(result: RetrievalResult) -> bool:
        return str(result.metadata.get("chunk_mode") or "") == "parent_child" and isinstance(
            result.metadata.get("parent_content"), str
        )

    @staticmethod
    def _result_display_text(result: RetrievalResult) -> str:
        parent_content = result.metadata.get("parent_content")
        if isinstance(parent_content, str) and parent_content.strip():
            return parent_content
        return result.chunk.content
