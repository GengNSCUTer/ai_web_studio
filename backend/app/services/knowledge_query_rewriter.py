from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


@dataclass(frozen=True)
class KnowledgeQueryRewriteResult:
    original_query: str
    rewritten_query: str
    did_rewrite: bool
    strategy: str = "none"
    reason: str = ""
    context_message_id: str | None = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "original_query": self.original_query,
            "rewritten_query": self.rewritten_query,
            "did_rewrite": self.did_rewrite,
            "strategy": self.strategy,
            "reason": self.reason,
            "context_message_id": self.context_message_id,
        }


class KnowledgeQueryRewriteService:
    """Deterministically expand short coreference questions for RAG retrieval.

    The service deliberately uses only prior user messages. Assistant text may be
    hallucinated and must not silently become retrieval intent. This is a bounded
    contextual expansion, not an LLM rewrite, query decomposition, or adaptive
    retrieval router.
    """

    STRATEGY = "user_context_expansion_v1"
    MAX_QUERY_CHARS = 240
    MAX_CONTEXT_CHARS = 480
    MAX_REWRITTEN_CHARS = 800
    CHINESE_COREFERENCE_PATTERN = re.compile(
        r"(它|它们|他们|这个|这些|那个|那些|这种|那种|"
        r"该(?:方案|方法|机制|设计|字段|服务|流程|项目|模型|工具|索引|表|接口)?|"
        r"上述|前者|后者|这里|那里|这一步|那一步|上一步|刚才说的)"
    )
    ENGLISH_COREFERENCE_PATTERN = re.compile(
        r"\b(it|they|them|this|that|these|those|former|latter|above|previous one)\b",
        flags=re.IGNORECASE,
    )

    def rewrite(
        self,
        *,
        query: str,
        recent_messages: list[object] | None = None,
    ) -> KnowledgeQueryRewriteResult:
        original_query = (query or "").strip()
        unchanged = KnowledgeQueryRewriteResult(
            original_query=original_query,
            rewritten_query=original_query,
            did_rewrite=False,
        )
        if not original_query or len(original_query) > self.MAX_QUERY_CHARS:
            return unchanged
        if not self._contains_coreference(original_query):
            return unchanged

        previous_user_message = self._latest_user_message(recent_messages or [])
        if previous_user_message is None:
            return unchanged
        context_message_id, context_text = previous_user_message
        rewritten_query = f"{context_text}；追问：{original_query}"[: self.MAX_REWRITTEN_CHARS].strip()
        if rewritten_query == original_query:
            return unchanged
        return KnowledgeQueryRewriteResult(
            original_query=original_query,
            rewritten_query=rewritten_query,
            did_rewrite=True,
            strategy=self.STRATEGY,
            reason="检测到依赖上文的短指代问题，使用最近一条用户问题扩展检索 Query。",
            context_message_id=context_message_id,
        )

    def _contains_coreference(self, query: str) -> bool:
        return bool(
            self.CHINESE_COREFERENCE_PATTERN.search(query)
            or self.ENGLISH_COREFERENCE_PATTERN.search(query)
        )

    def _latest_user_message(self, messages: list[object]) -> tuple[str | None, str] | None:
        for message in reversed(messages[-12:]):
            role = self._field(message, "role")
            content = str(self._field(message, "content") or "").strip()
            if role != "user" or not content:
                continue
            bounded_content = content[: self.MAX_CONTEXT_CHARS].strip()
            if not bounded_content:
                continue
            message_id = self._field(message, "id")
            return (str(message_id) if message_id else None, bounded_content)
        return None

    @staticmethod
    def _field(message: object, name: str) -> Any:
        return message.get(name) if isinstance(message, dict) else getattr(message, name, None)
