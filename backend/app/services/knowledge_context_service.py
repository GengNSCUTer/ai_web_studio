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
)
from app.repositories.setting_repo import UserSettingRepository
from app.services.knowledge_index_service import KnowledgeIndexService, RetrievalResult
from app.services.setting_service import SettingService
from app.services.tools.schemas import ExternalSource


@dataclass(frozen=True)
class KnowledgeContextResult:
    context_text: str | None
    sources: list[ExternalSource] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)


class KnowledgeContextService:
    """Retrieves indexed knowledge chunks and formats them for chat context."""

    def __init__(self, *, db: Session, user_id: str, index_service: KnowledgeIndexService | None = None) -> None:
        self.db = db
        self.user_id = user_id
        self.base_repo = KnowledgeBaseRepository(db)
        self.document_repo = KnowledgeDocumentRepository(db)
        self.chunk_repo = KnowledgeChunkRepository(db)
        self.setting_service = SettingService(UserSettingRepository(db))
        self.index_service = index_service

    async def build_context(
        self,
        *,
        knowledge_base_id: str | None,
        query: str,
    ) -> KnowledgeContextResult:
        if not knowledge_base_id:
            return self._empty(enabled=False)

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
            index_service = self.index_service or KnowledgeIndexService(
                chunk_repo=self.chunk_repo,
                document_repo=self.document_repo,
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
        sources = self._build_sources(
            knowledge_base_id=knowledge_base.id,
            knowledge_base_name=knowledge_base.name,
            results=selected,
        )

        return KnowledgeContextResult(
            context_text=context_text,
            sources=sources,
            notices=[],
            diagnostics={
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
            },
            details={
                "knowledge_sources": [source.to_public_dict() for source in sources],
            },
        )

    @staticmethod
    def _empty(
        *,
        enabled: bool,
        error: int = 0,
        knowledge_base_id: str | None = None,
        knowledge_base_name: str | None = None,
        notices: list[str] | None = None,
        latency_ms: int = 0,
    ) -> KnowledgeContextResult:
        return KnowledgeContextResult(
            context_text=None,
            sources=[],
            notices=notices or [],
            diagnostics={
                "knowledge_retrieval_enabled": int(enabled),
                "knowledge_base_id": knowledge_base_id or "",
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
    def _format_context(*, knowledge_base_name: str, results: list[RetrievalResult], max_chars: int) -> str | None:
        if not results:
            return None
        lines = [f"知识库：{knowledge_base_name}", "请优先依据以下知识库片段回答；若片段不足以回答，请明确说明不确定。"]
        used_chars = sum(len(line) for line in lines)
        for index, result in enumerate(results, start=1):
            file_name = str(result.metadata.get("file_name") or "unknown")
            score = result.rerank_score if result.rerank_score is not None else result.score
            header = f"\n[KB{index}] 文件：{file_name}；chunk：{result.chunk.chunk_index}；score：{score:.4f}"
            content = result.chunk.content.strip()
            remaining = max_chars - used_chars - len(header)
            if remaining <= 120:
                break
            if len(content) > remaining:
                content = content[:remaining].rstrip() + "..."
            lines.append(f"{header}\n{content}")
            used_chars += len(header) + len(content)
        return "\n".join(lines).strip() if len(lines) > 2 else None

    @staticmethod
    def _build_sources(
        *,
        knowledge_base_id: str,
        knowledge_base_name: str,
        results: list[RetrievalResult],
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
                "vector_score": result.score,
                "rerank_score": result.rerank_score,
                "rank_source": result.rank_source,
                "source_start": result.chunk.source_start,
                "source_end": result.chunk.source_end,
                "tool": "knowledge_retrieval",
            }
            sources.append(
                ExternalSource(
                    source_type="knowledge",
                    provider="knowledge_base",
                    title=f"{knowledge_base_name} / {file_name}",
                    display_text=result.chunk.content[:1200],
                    rank=index,
                    score=score,
                    used_in_prompt=True,
                    citation_label=f"[KB{index}]",
                    metadata=json.loads(json.dumps(metadata, ensure_ascii=False, default=str)),
                )
            )
        return sources
