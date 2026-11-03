from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field, replace
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
from app.services.knowledge_query_rewriter import (
    KnowledgeQueryRewriteResult,
    KnowledgeQueryRewriteService,
)
from app.services.knowledge_retrieval_pipeline import KnowledgeRetrievalPipeline
from app.services.knowledge_error import (
    classify_knowledge_error,
    public_knowledge_error_message,
)
from app.services.setting_service import SettingService
from app.services.tools.schemas import ExternalSource
from app.services.knowledge_parent_context import unique_parent_results, bounded_evidence
from app.services.evidence_context import EvidenceSection, build_evidence_section, complete_excerpt


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KnowledgeContextResult:
    context_text: str | None
    sources: list[ExternalSource] = field(default_factory=list)
    notices: list[str] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)
    retrieval_log_id: str | None = None
    retrieval_log_ids: list[str] = field(default_factory=list)
    evidence_section: EvidenceSection | None = None


class KnowledgeContextService:
    """Retrieves indexed knowledge chunks and formats them for chat context."""

    def __init__(
        self,
        *,
        db: Session,
        user_id: str,
        index_service: object | None = None,
        query_rewriter: KnowledgeQueryRewriteService | None = None,
    ) -> None:
        self.db = db
        self.user_id = user_id
        self.base_repo = KnowledgeBaseRepository(db)
        self.document_repo = KnowledgeDocumentRepository(db)
        self.chunk_repo = KnowledgeChunkRepository(db)
        self.retrieval_log_repo = KnowledgeRetrievalLogRepository(db)
        self.setting_service = SettingService(UserSettingRepository(db))
        self.index_service = index_service
        self.query_rewriter = query_rewriter or KnowledgeQueryRewriteService()

    async def build_context(
        self,
        *,
        knowledge_base_id: str | None,
        knowledge_base_ids: list[str] | None = None,
        query: str,
        recent_messages: list[object] | None = None,
        rewrite_provider: dict[str, str | None] | None = None,
    ) -> KnowledgeContextResult:
        resolved_ids = self._normalize_knowledge_base_ids(knowledge_base_id, knowledge_base_ids)
        if not resolved_ids:
            return self._empty(enabled=False)
        rewrite = await self.query_rewriter.rewrite_async(
            query=query,
            recent_messages=recent_messages,
            **(rewrite_provider or {}),
        )
        if len(resolved_ids) == 1:
            return await self._build_single_context(
                knowledge_base_id=resolved_ids[0],
                original_query=rewrite.original_query,
                retrieval_query=rewrite.rewritten_query,
                rewrite=rewrite,
            )

        if not rewrite.original_query:
            return self._empty(
                enabled=True,
                knowledge_base_ids=resolved_ids,
                notices=["当前问题为空，已跳过知识库检索。"],
            )

        started_at = time.monotonic()
        partials = await asyncio.gather(
            *[
                self._build_single_context(
                    knowledge_base_id=base_id,
                    original_query=rewrite.original_query,
                    retrieval_query=rewrite.rewritten_query,
                    rewrite=rewrite,
                )
                for base_id in resolved_ids
            ],
        )
        latency_ms = int((time.monotonic() - started_at) * 1000)
        notices = [notice for partial in partials for notice in partial.notices]
        all_sources = [source for partial in partials for source in partial.sources]
        ranked_sources = sorted(
            all_sources,
            key=lambda source: float(source.score or 0),
            reverse=True,
        )
        max_context_chars = self._multi_context_char_budget(resolved_ids)
        all_results = self._prepare_multi_sources(self._relabel_sources(ranked_sources), max_context_chars)
        injected_sources = all_results
        evidence_section = self._evidence_section(injected_sources).fit(lambda value: len(value.render()) <= max_context_chars)
        context_text = evidence_section.render() if evidence_section.units else None
        retrieval_log_ids = [partial.retrieval_log_id for partial in partials if partial.retrieval_log_id]
        diagnostics = self._merge_multi_diagnostics(
            partials=partials,
            knowledge_base_ids=resolved_ids,
            latency_ms=latency_ms,
            context_text=context_text,
            injected_count=len(evidence_section.units),
            retrieval_log_ids=retrieval_log_ids,
            rewrite=rewrite,
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
                "knowledge_query_rewrite": rewrite.to_public_dict(),
            },
            retrieval_log_id=retrieval_log_ids[0] if retrieval_log_ids else None,
            retrieval_log_ids=retrieval_log_ids,
            evidence_section=evidence_section,
        )

    async def _build_single_context(
        self,
        *,
        knowledge_base_id: str,
        original_query: str,
        retrieval_query: str,
        rewrite: KnowledgeQueryRewriteResult,
    ) -> KnowledgeContextResult:
        knowledge_base = self.base_repo.get_by_user(knowledge_base_id, self.user_id)
        if not knowledge_base:
            return self._empty(
                enabled=True,
                error=1,
                notices=["所选知识库不存在或无权访问，已跳过知识库检索。"],
            )

        if not original_query:
            return self._empty(
                enabled=True,
                knowledge_base_id=knowledge_base.id,
                knowledge_base_name=knowledge_base.name,
                notices=["当前问题为空，已跳过知识库检索。"],
            )

        total_chunks = self.chunk_repo.count_by_knowledge_base(
            knowledge_base.id,
            self.user_id,
            index_generation=knowledge_base.active_index_generation or "legacy",
        )
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
                    query=retrieval_query,
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
            error_code = classify_knowledge_error(exc)
            # 异常文本可能带有远端 URL、查询参数或凭据，日志只记录稳定错误码和类型。
            logger.warning(
                "knowledge context retrieval failed: knowledge_base_id=%s error_code=%s exception_type=%s",
                knowledge_base.id,
                error_code,
                type(exc).__name__,
            )
            return self._empty(
                enabled=True,
                error=1,
                knowledge_base_id=knowledge_base.id,
                knowledge_base_name=knowledge_base.name,
                notices=[
                    public_knowledge_error_message(
                        error_code,
                        knowledge_base_name=knowledge_base.name,
                    )
                ],
                latency_ms=int((time.monotonic() - started_at) * 1000),
            )
        latency_ms = int((time.monotonic() - started_at) * 1000)

        unique_results = unique_parent_results(results)
        selected = self._prepare_context_results(
            knowledge_base_name=knowledge_base.name,
            results=unique_results[:max(1, knowledge_base.max_context_chunks)],
            max_chars=knowledge_base.max_context_chars,
        )
        injected_count = len(selected)
        injected_results = selected[:injected_count]
        base_diagnostics = {
            "knowledge_retrieval_enabled": 1,
            "knowledge_base_id": knowledge_base.id,
            "knowledge_base_name": knowledge_base.name,
            "knowledge_chunks_total": total_chunks,
            "knowledge_chunks_retrieved": len(results),
            "knowledge_parent_duplicates_removed": len(results) - len(unique_results),
            "knowledge_parent_expansions_truncated": sum(bool(item.metadata.get("parent_expansion_truncated")) for item in injected_results),
            "knowledge_chunks_injected": len(injected_results),
            "knowledge_context_chars": 0,
            "knowledge_rerank_enabled": int(bool(knowledge_base.rerank_enabled)),
            "knowledge_rerank_used": int(any(result.rerank_score is not None for result in injected_results)),
            "knowledge_retrieval_latency_ms": latency_ms,
            "knowledge_query_rewrite_used": int(rewrite.did_rewrite),
            "knowledge_query_rewrite_strategy": rewrite.strategy,
        }
        log_diagnostics = {
            **base_diagnostics,
            "knowledge_original_query": original_query,
            "knowledge_retrieval_query": retrieval_query,
            "knowledge_query_rewrite_reason": rewrite.reason,
            "knowledge_query_rewrite_context_message_id": rewrite.context_message_id,
        }
        retrieval_log = self.retrieval_log_repo.create(
            user_id=self.user_id,
            knowledge_base_id=knowledge_base.id,
            query=original_query,
            retrieval_mode=knowledge_base.retrieval_mode,
            top_k=knowledge_base.retrieval_top_k,
            rerank_enabled=bool(knowledge_base.rerank_enabled),
            rerank_model=knowledge_base.rerank_model if knowledge_base.rerank_enabled else None,
            candidates=self._serialize_results(results),
            selected=self._serialize_results(injected_results),
            diagnostics=log_diagnostics,
            sources=[],
            status="success",
            elapsed_ms=latency_ms,
        )
        sources = self._build_sources(
            knowledge_base_id=knowledge_base.id,
            knowledge_base_name=knowledge_base.name,
            results=injected_results,
            retrieval_log_id=retrieval_log.id,
        )
        evidence_section = self._evidence_section(sources).fit(
            lambda value: len(value.render()) <= knowledge_base.max_context_chars)
        base_diagnostics.update({"knowledge_chunks_selected": len(injected_results),
                                 "knowledge_chunks_injected": len(evidence_section.units),
                                 "knowledge_context_chars": len(evidence_section.render()) if evidence_section.units else 0})
        retrieval_log.diagnostics_json = json.dumps({**log_diagnostics, **base_diagnostics}, ensure_ascii=False)
        self.retrieval_log_repo.save(retrieval_log)
        return KnowledgeContextResult(
            context_text=evidence_section.render() if evidence_section.units else None,
            sources=sources,
            notices=[],
            diagnostics={**base_diagnostics, "knowledge_retrieval_log_id": retrieval_log.id},
            details={
                "knowledge_sources": [source.to_public_dict() for source in sources],
                "knowledge_retrieval_log_id": retrieval_log.id,
                "knowledge_retrieval_log_ids": [retrieval_log.id],
                "knowledge_query_rewrite": rewrite.to_public_dict(),
            },
            retrieval_log_id=retrieval_log.id,
            retrieval_log_ids=[retrieval_log.id],
            evidence_section=evidence_section,
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
    def _evidence_section(sources: list[ExternalSource]) -> EvidenceSection:
        multi = len({source.metadata.get("knowledge_base_id") for source in sources}) > 1
        return build_evidence_section("knowledge_context", sources,
            intro=("多知识库检索。" if multi else "") + "请依据以下知识库证据回答；不足时说明不确定，引用保留的[KBn]来源。")

    @staticmethod
    def _context_header(index: int, result: RetrievalResult) -> str:
        file_name = str(result.metadata.get("file_name") or "unknown")
        score = result.rerank_score if result.rerank_score is not None else result.score
        expansion = "；parent-child：已扩展" if KnowledgeContextService._is_parent_child_result(result) else ""
        return f"\n[KB{index}] 文件：{file_name}；chunk：{result.chunk.chunk_index}；score：{score:.4f}{expansion}"

    @staticmethod
    def _prepare_context_results(*, knowledge_base_name: str, results: list[RetrievalResult], max_chars: int) -> list[RetrievalResult]:
        """先去重再按候选数分配预算，避免大父块吃掉其他证据的全部空间。"""
        results = unique_parent_results(results)
        prefix = f"知识库：{knowledge_base_name}\n请优先依据以下知识库片段回答；若片段不足以回答，请明确说明不确定。"
        remaining = max_chars - len(prefix)
        selected = []
        expand_parents = any(KnowledgeContextService._is_parent_child_result(item) for item in results)
        for index, result in enumerate(results, 1):
            header = KnowledgeContextService._context_header(index, result)
            capacity = (remaining // (len(results) - index + 1) if expand_parents else remaining) - len(header) - 2
            if capacity <= 120:
                # 极小窗口至少允许最佳证据，仍不突破总预算。
                capacity = remaining - len(header) - 2
            if capacity <= 120:
                break
            existing = result.metadata.get("context_content")
            content = existing[:capacity] if isinstance(existing, str) else bounded_evidence(result, capacity)
            content = content.strip()
            original = (result.metadata.get("parent_content") if KnowledgeContextService._is_parent_child_result(result)
                        else result.chunk.content).strip()
            if len(content) < len(original) and (not isinstance(existing, str) or len(existing) > capacity):
                safe = complete_excerpt(content, len(content) - 1)
                child = result.chunk.content.strip()
                # 父块取窗无法保留完整命中区时，退回完整Child，而不是保留半个句子/代码。
                if child in content and child not in safe and len(child) <= capacity:
                    safe = child
                content = safe
            if not content:
                continue
            selected.append(replace(result, metadata={**result.metadata, "context_content": content,
                "evidence_original_body_chars": len(original), "evidence_body_start": max(0, original.find(content)),
                "evidence_pretruncated": len(content) < len(original),
                "parent_expansion_truncated": bool(KnowledgeContextService._is_parent_child_result(result)
                    and len(content) < len(result.metadata["parent_content"]))}))
            remaining -= len(header) + 2 + len(content)
        return selected

    @staticmethod
    def _format_context(*, knowledge_base_name: str, results: list[RetrievalResult], max_chars: int) -> str | None:
        results = KnowledgeContextService._prepare_context_results(
            knowledge_base_name=knowledge_base_name, results=results, max_chars=max_chars)
        if not results:
            return None
        lines = [f"知识库：{knowledge_base_name}", "请优先依据以下知识库片段回答；若片段不足以回答，请明确说明不确定。"]
        used_chars = sum(len(line) for line in lines)
        for index, result in enumerate(results, start=1):
            header = KnowledgeContextService._context_header(index, result)
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
    def _prepare_multi_sources(results: list[ExternalSource], max_chars: int) -> list[ExternalSource]:
        """跨库合并仍限制父块预算，来源正文与实际注入保持一致。"""
        remaining = max_chars - len("知识库：多知识库检索\n请优先依据以下知识库片段回答；若片段不足以回答，请明确说明不确定。")
        selected = []
        has_parents = any(item.metadata.get("chunk_mode") == "parent_child" for item in results)
        for index, source in enumerate(results, 1):
            meta = source.metadata
            header = f"\n[KB{index}] 知识库：{meta.get('knowledge_base_name') or 'unknown'}；文件：{meta.get('file_name') or 'unknown'}；chunk：{meta.get('chunk_index', '')}；score：{float(source.score or 0):.4f}"
            capacity = (remaining // (len(results) - index + 1) if has_parents else remaining) - len(header) - 2
            if capacity <= 120:
                capacity = remaining - len(header) - 2
            if capacity <= 120:
                break
            text = source.display_text.strip()
            anchor = max(0, int(meta.get("context_child_offset", 0)))
            start = min(max(0, anchor - capacity // 3), max(0, len(text) - capacity))
            raw_content = text[start:start + capacity]
            content = raw_content.strip()
            if len(content) < len(text):
                content = complete_excerpt(content, len(content) - 1)
            if not content:
                continue
            selected.append(replace(source, display_text=content, metadata={**meta,
                "evidence_pretruncated": bool(meta.get("evidence_pretruncated") or len(content) < len(text)),
                "evidence_original_body_chars": max(len(text), int(meta.get("evidence_original_body_chars") or 0)),
                "evidence_body_start": int(meta.get("evidence_body_start") or 0) + start
                                       + len(raw_content) - len(raw_content.lstrip()),
                "context_child_offset": max(0, anchor - start - (len(raw_content) - len(raw_content.lstrip()))),
                "parent_expansion_truncated": bool(meta.get("parent_expansion_truncated") or (has_parents and len(content) < len(text)))}))
            remaining -= len(header) + 2 + len(content)
        return selected

    @staticmethod
    def _format_multi_context(*, results: list[ExternalSource], max_chars: int) -> str | None:
        results = KnowledgeContextService._prepare_multi_sources(results, max_chars)
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
        rewrite: KnowledgeQueryRewriteResult,
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
            "knowledge_parent_duplicates_removed": sum(int(partial.diagnostics.get("knowledge_parent_duplicates_removed", 0)) for partial in partials),
            "knowledge_chunks_injected": injected_count,
            "knowledge_context_chars": len(context_text or ""),
            "knowledge_rerank_enabled": rerank_enabled,
            "knowledge_rerank_used": rerank_used,
            "knowledge_retrieval_error": int(errors > 0),
            "knowledge_retrieval_latency_ms": latency_ms,
            "knowledge_retrieval_log_id": retrieval_log_ids[0] if retrieval_log_ids else "",
            "knowledge_retrieval_log_ids": ",".join(retrieval_log_ids),
            "knowledge_query_rewrite_used": int(rewrite.did_rewrite),
            "knowledge_query_rewrite_strategy": rewrite.strategy,
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
                    "metadata": {k: v for k, v in result.metadata.items() if k not in {"parent_content", "context_content"}},
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
                **{k: v for k, v in result.metadata.items() if k not in {"parent_content", "context_content"}},
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
            metadata["context_child_offset"] = max(0, display_text.find(result.chunk.content.strip()))
            sources.append(
                ExternalSource(
                    source_type="knowledge",
                    provider="knowledge_base",
                    title=f"{knowledge_base_name} / {file_name}",
                    display_text=display_text,
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
        content = result.metadata.get("context_content")
        if isinstance(content, str):
            return content
        parent_content = result.metadata.get("parent_content")
        if isinstance(parent_content, str) and parent_content.strip():
            return parent_content
        return result.chunk.content
