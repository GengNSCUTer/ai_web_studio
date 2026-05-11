from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class AttachmentChunk:
    attachment_id: str | None
    file_name: str
    index: int
    text: str
    score: int


@dataclass(frozen=True)
class AttachmentContextResult:
    context_text: str | None
    chunks: list[AttachmentChunk] = field(default_factory=list)
    diagnostics: dict[str, int] = field(default_factory=dict)


class AttachmentContextService:
    CHUNK_SIZE = 1200
    CHUNK_OVERLAP = 160
    MAX_CHUNKS_PER_FILE = 80

    def build_context(
        self,
        *,
        attachments: list[object],
        query: str,
        max_chars: int,
    ) -> AttachmentContextResult:
        file_attachments = [item for item in attachments if getattr(item, "kind", None) == "file"]
        chunks = self._build_chunks(file_attachments=file_attachments, query=query)
        selected, used_chars = self._select_chunks(chunks=chunks, max_chars=max_chars)

        truncated_chunks = max(0, len(chunks) - len(selected))
        truncated_chars = max(0, sum(len(chunk.text) for chunk in chunks) - used_chars)
        if not selected:
            return AttachmentContextResult(
                context_text=None,
                chunks=[],
                diagnostics={
                    "attachment_files_seen": len(file_attachments),
                    "attachment_chunks_total": len(chunks),
                    "attachment_chunks_selected": 0,
                    "attachment_context_chars": 0,
                    "attachment_truncated_chunks": truncated_chunks,
                    "attachment_truncated_chars": truncated_chars,
                },
            )

        context_parts = [
            f"[附件片段: {chunk.file_name} #{chunk.index + 1} | score={chunk.score}]\n{chunk.text}"
            for chunk in selected
        ]
        context_text = "\n\n".join(context_parts).strip()
        return AttachmentContextResult(
            context_text=context_text,
            chunks=selected,
            diagnostics={
                "attachment_files_seen": len(file_attachments),
                "attachment_chunks_total": len(chunks),
                "attachment_chunks_selected": len(selected),
                "attachment_context_chars": len(context_text),
                "attachment_truncated_chunks": truncated_chunks,
                "attachment_truncated_chars": truncated_chars,
            },
        )

    def _build_chunks(self, *, file_attachments: list[object], query: str) -> list[AttachmentChunk]:
        query_terms = self._extract_terms(query)
        chunks: list[AttachmentChunk] = []
        for attachment in file_attachments:
            parsed_text = self._normalize_text(getattr(attachment, "parsed_text", None) or "")
            if not parsed_text:
                continue

            file_name = getattr(attachment, "file_name", None) or "attachment"
            attachment_id = getattr(attachment, "id", None)
            for index, chunk_text in enumerate(self._split_text(parsed_text)):
                score = self._score_chunk(chunk_text, query_terms=query_terms, index=index)
                chunks.append(
                    AttachmentChunk(
                        attachment_id=attachment_id,
                        file_name=file_name,
                        index=index,
                        text=chunk_text,
                        score=score,
                    )
                )

        chunks.sort(key=lambda item: (-item.score, item.file_name, item.index))
        return chunks

    @classmethod
    def _split_text(cls, text: str) -> list[str]:
        if len(text) <= cls.CHUNK_SIZE:
            return [text]

        chunks: list[str] = []
        start = 0
        while start < len(text) and len(chunks) < cls.MAX_CHUNKS_PER_FILE:
            end = min(len(text), start + cls.CHUNK_SIZE)
            chunk = text[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= len(text):
                break
            start = max(end - cls.CHUNK_OVERLAP, start + 1)
        return chunks

    @staticmethod
    def _normalize_text(text: str) -> str:
        return re.sub(r"\s+", " ", text).strip()

    @staticmethod
    def _extract_terms(query: str) -> set[str]:
        normalized = query.lower()
        ascii_terms = {
            item
            for item in re.findall(r"[a-zA-Z0-9_]{2,}", normalized)
            if len(item) >= 2
        }
        cjk_terms = {
            item
            for item in re.findall(r"[\u4e00-\u9fff]{2,}", normalized)
            if len(item) >= 2
        }

        cjk_bigrams: set[str] = set()
        for term in cjk_terms:
            cjk_bigrams.update(term[index : index + 2] for index in range(max(0, len(term) - 1)))

        return ascii_terms | cjk_terms | cjk_bigrams

    @staticmethod
    def _score_chunk(text: str, *, query_terms: set[str], index: int) -> int:
        normalized = text.lower()
        score = 0
        for term in query_terms:
            if term in normalized:
                score += 10 + min(normalized.count(term), 5)

        # 文件开头通常包含标题、摘要、目录等高价值信息，给一个轻量先验。
        if index == 0:
            score += 3
        elif index <= 2:
            score += 1
        return score

    @staticmethod
    def _select_chunks(*, chunks: list[AttachmentChunk], max_chars: int) -> tuple[list[AttachmentChunk], int]:
        if not chunks or max_chars <= 0:
            return [], 0

        selected: list[AttachmentChunk] = []
        used_chars = 0
        limit = max(800, max_chars)
        for chunk in chunks:
            next_cost = len(chunk.text) + len(chunk.file_name) + 64
            if selected and used_chars + next_cost > limit:
                continue
            if not selected and next_cost > limit:
                selected.append(
                    AttachmentChunk(
                        attachment_id=chunk.attachment_id,
                        file_name=chunk.file_name,
                        index=chunk.index,
                        text=chunk.text[: max(200, limit - len(chunk.file_name) - 64)].strip(),
                        score=chunk.score,
                    )
                )
                used_chars = limit
                break
            selected.append(chunk)
            used_chars += next_cost
        selected.sort(key=lambda item: (item.file_name, item.index))
        return selected, used_chars
