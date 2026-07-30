import difflib
import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any
from datetime import datetime, timezone

from app.models.user_memory import UserMemory
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.memory_repo import UserMemoryRepository
from app.schemas.memory import MemorySuggestion, UserMemoryCreate, UserMemoryResponse, UserMemoryUpdate


@dataclass(frozen=True)
class MemoryContextSelection:
    """The memory subset chosen for one user query.

    Long-term memory is evidence, not a second conversation history.  Keeping the
    selection separate makes the lexical first pass replaceable with embedding
    retrieval later without changing prompt assembly.
    """

    memories: list[UserMemory]
    relevant_count: int
    always_on_count: int


class MemoryService:
    VALID_MEMORY_TYPES = {"profile", "project", "fact", "instruction"}
    TYPE_LABELS = {
        "profile": "用户偏好",
        "project": "项目背景",
        "fact": "重要事实",
        "instruction": "长期指令",
    }
    SENSITIVE_VALUE_PATTERN = re.compile(
        r"(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|passwd|密码|密钥|令牌)"
        r"\s*[:=：]\s*[^\s,，;；]{6,}",
        flags=re.IGNORECASE,
    )
    EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", flags=re.IGNORECASE)
    PHONE_PATTERN = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
    ID_CARD_PATTERN = re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)")
    VOLATILE_TIME_PATTERN = re.compile(
        r"(?:今天|明天|后天|本周|这周|下周|这个月|下个月|当前临时|暂时|最近|"
        r"today|tomorrow|this week|next week|currently|temporary)",
        flags=re.IGNORECASE,
    )

    def __init__(self, repo: UserMemoryRepository, conversation_repo: ConversationRepository | None = None):
        self.repo = repo
        self.conversation_repo = conversation_repo

    @classmethod
    def normalize_memory_type(cls, value: str | None) -> str:
        normalized = (value or "").strip() or "fact"
        if normalized not in cls.VALID_MEMORY_TYPES:
            return "fact"
        return normalized

    @staticmethod
    def normalize_text(value: str | None) -> str:
        return " ".join((value or "").split()).strip()

    def _memory_response(self, memory: UserMemory, user_id: str) -> UserMemoryResponse:
        response = UserMemoryResponse.model_validate(memory)
        if memory.source_conversation_id and self.conversation_repo:
            conversation = self.conversation_repo.get_by_user(memory.source_conversation_id, user_id)
            if conversation:
                response.source_conversation_title = conversation.title
        return response

    def list_memories(self, user_id: str) -> list[UserMemoryResponse]:
        expire_due = getattr(self.repo, "expire_due", None)
        if expire_due:
            expire_due(user_id)
        return [self._memory_response(item, user_id) for item in self.repo.list_by_user(user_id)]

    def create_memory(self, user_id: str, payload: UserMemoryCreate) -> UserMemoryResponse:
        normalized_content = self.normalize_text(payload.content)
        project_id = None
        if payload.source_conversation_id and self.conversation_repo:
            conversation = self.conversation_repo.get_by_user(payload.source_conversation_id, user_id)
            project_id = getattr(conversation, "project_id", None) if conversation else None
        memory = UserMemory(
            user_id=user_id,
            memory_type=self.normalize_memory_type(payload.memory_type),
            title=self.normalize_text(payload.title)[:120] or "未命名记忆",
            content=normalized_content,
            source="manual",
            source_conversation_id=self.normalize_text(payload.source_conversation_id) or None,
            source_message_ids=self.normalize_text(payload.source_message_ids) or None,
            confidence=self.normalize_text(payload.confidence) or None,
            is_enabled=payload.is_enabled,
            status="active",
            project_id=project_id,
            content_hash=self.memory_content_hash(
                user_id=user_id,
                memory_type=self.normalize_memory_type(payload.memory_type),
                content=normalized_content,
                project_id=project_id,
            ),
        )
        saved = self.repo.save(memory)
        return self._memory_response(saved, user_id)

    @classmethod
    def memory_content_hash(
        cls,
        *,
        user_id: str,
        memory_type: str,
        content: str,
        project_id: str | None,
    ) -> str:
        canonical = "|".join(
            (user_id, cls.normalize_memory_type(memory_type), project_id or "global", cls.normalize_text(content).lower())
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def approve_candidate(
        self,
        *,
        memory: UserMemory,
        expires_at: datetime | None = None,
    ) -> UserMemoryResponse:
        if memory.status == "active":
            return self._memory_response(memory, memory.user_id)
        if memory.status != "pending":
            raise ValueError("只有 pending 候选可以确认")
        if memory.risk_level == "duplicate":
            raise ValueError("重复候选没有激活价值，请直接拒绝")
        if memory.risk_level == "volatile" and expires_at is None:
            raise ValueError("短期候选必须设置 expires_at")
        if expires_at is not None:
            normalized_expiry = expires_at
            if normalized_expiry.tzinfo is None:
                normalized_expiry = normalized_expiry.replace(tzinfo=timezone.utc)
            if normalized_expiry <= datetime.now(timezone.utc):
                raise ValueError("expires_at 必须晚于当前时间")
            memory.expires_at = normalized_expiry

        if memory.supersedes_memory_id:
            previous = self.repo.get_by_user(memory.supersedes_memory_id, memory.user_id)
            if previous and previous.status == "active":
                previous.status = "superseded"
                previous.is_enabled = False
                self.repo.flush(previous)
        memory.status = "active"
        memory.is_enabled = True
        memory.review_at = datetime.now(timezone.utc)
        saved = self.repo.save(memory)
        return self._memory_response(saved, memory.user_id)

    def reject_candidate(self, *, memory: UserMemory) -> UserMemoryResponse:
        if memory.status == "rejected":
            return self._memory_response(memory, memory.user_id)
        if memory.status != "pending":
            raise ValueError("只有 pending 候选可以拒绝")
        memory.status = "rejected"
        memory.is_enabled = False
        memory.review_at = datetime.now(timezone.utc)
        saved = self.repo.save(memory)
        return self._memory_response(saved, memory.user_id)

    def update_memory(
        self,
        *,
        memory: UserMemory,
        payload: UserMemoryUpdate,
    ) -> UserMemoryResponse:
        data = payload.model_dump(exclude_unset=True)
        if "memory_type" in data:
            memory.memory_type = self.normalize_memory_type(data["memory_type"])
        if "title" in data and data["title"] is not None:
            memory.title = self.normalize_text(data["title"])[:120] or memory.title
        if "content" in data and data["content"] is not None:
            memory.content = self.normalize_text(data["content"])
        if "is_enabled" in data and data["is_enabled"] is not None:
            if memory.status != "active" and data["is_enabled"]:
                raise ValueError("非 active 记忆不能直接启用，请使用候选审核接口")
            memory.is_enabled = data["is_enabled"]
        if "source_conversation_id" in data:
            memory.source_conversation_id = self.normalize_text(data["source_conversation_id"]) or None
        if "source_message_ids" in data:
            memory.source_message_ids = self.normalize_text(data["source_message_ids"]) or None
        if "confidence" in data:
            memory.confidence = self.normalize_text(data["confidence"]) or None

        saved = self.repo.save(memory)
        return self._memory_response(saved, memory.user_id)

    def select_memories_for_query(
        self,
        user_id: str,
        *,
        query: str | None,
        project_id: str | None = None,
        max_memories: int = 8,
    ) -> MemoryContextSelection:
        expire_due = getattr(self.repo, "expire_due", None)
        if expire_due:
            expire_due(user_id)
        memories = self.repo.list_by_user(user_id, enabled_only=True)
        memories = [
            memory
            for memory in memories
            if self._memory_matches_project_scope(memory=memory, user_id=user_id, project_id=project_id)
        ]
        if not memories:
            return MemoryContextSelection(memories=[], relevant_count=0, always_on_count=0)

        # Existing callers that do not have a query retain the historical,
        # recency-based behavior.  Chat assembly always provides the current query.
        normalized_query = self.normalize_text(query)
        if not normalized_query:
            return MemoryContextSelection(
                memories=memories[:max_memories],
                relevant_count=len(memories[:max_memories]),
                always_on_count=0,
            )

        always_on: list[tuple[UserMemory, float]] = []
        relevant: list[tuple[UserMemory, float]] = []
        for memory in memories:
            title = self.normalize_text(getattr(memory, "title", ""))
            content = self.normalize_text(getattr(memory, "content", ""))
            score = self._query_relevance_score(normalized_query, f"{title}\n{content}")
            if memory.memory_type in {"instruction", "profile"}:
                # Explicit preferences and durable user instructions should not
                # disappear merely because their wording differs from this turn.
                always_on.append((memory, score))
            elif score > 0:
                relevant.append((memory, score))

        # The repository is already sorted by recency. Python's stable sort keeps
        # that deterministic tie-breaker, which makes prompt-cache diagnostics
        # stable between identical requests.
        always_on.sort(key=lambda item: item[1], reverse=True)
        relevant.sort(key=lambda item: item[1], reverse=True)
        selected = [memory for memory, _ in [*always_on, *relevant][:max_memories]]
        return MemoryContextSelection(
            memories=selected,
            relevant_count=sum(1 for memory, _ in relevant if memory in selected),
            always_on_count=sum(1 for memory, _ in always_on if memory in selected),
        )

    def build_memory_context(
        self,
        user_id: str,
        *,
        max_chars: int,
        query: str | None = None,
        project_id: str | None = None,
    ) -> tuple[str | None, int, int]:
        selection = self.select_memories_for_query(user_id, query=query, project_id=project_id)
        memories = selection.memories
        if not memories:
            return None, 0, 0

        header = "以下是用户显式保存的长期记忆，请在不违背当前对话的前提下参考："
        chunks: list[str] = []
        total_chars = len(header) + 1
        limit = max(500, min(max_chars, 20000))

        for memory in memories:
            title = self.normalize_text(memory.title)
            content = self.normalize_text(memory.content)
            if not content:
                continue
            label = self.TYPE_LABELS.get(memory.memory_type, "长期记忆")
            line = f"- [{label}] {title}: {content}"
            next_total = total_chars + len(line) + (1 if chunks else 0)
            if next_total > limit:
                # 单条异常长记忆不能阻塞后续所有短记忆；也不截断事实，避免把半句话注入模型。
                continue
            chunks.append(line)
            total_chars = next_total

        if not chunks:
            return None, 0, 0

        context = header + "\n" + "\n".join(chunks)
        return context, len(chunks), len(context)

    def _memory_matches_project_scope(
        self,
        *,
        memory: UserMemory,
        user_id: str,
        project_id: str | None,
    ) -> bool:
        """Keep conversation-sourced project memories inside their project.

        Legacy manually-created project memories have no source conversation and
        therefore retain their historical global behavior. New suggested project
        memories carry source_conversation_id and fail closed when their source
        can no longer be resolved.
        """

        if getattr(memory, "memory_type", None) != "project":
            return True
        explicit_project_id = self.normalize_text(getattr(memory, "project_id", None))
        if explicit_project_id:
            return explicit_project_id == project_id
        source_conversation_id = self.normalize_text(getattr(memory, "source_conversation_id", None))
        if not source_conversation_id:
            return True
        if not self.conversation_repo:
            return False
        conversation = self.conversation_repo.get_by_user(source_conversation_id, user_id)
        if not conversation:
            return False
        return getattr(conversation, "project_id", None) == project_id

    @classmethod
    def _query_relevance_score(cls, query: str, candidate: str) -> float:
        """Return a deterministic lexical relevance score for Chinese and English.

        This is deliberately a conservative first-stage filter, not a claim of
        semantic retrieval. English identifiers/words and Chinese character
        bigrams are both useful signals without adding an embedding-model
        dependency or mixing incompatible memory vectors into the current schema.
        """

        query_terms = cls._search_terms(query)
        candidate_terms = cls._search_terms(candidate)
        if not query_terms or not candidate_terms:
            return 0.0
        overlap = query_terms & candidate_terms
        if not overlap:
            return 0.0
        # Normalise by query size: matching the key terms of a focused question
        # matters more than a large memory document sharing many generic terms.
        return round(len(overlap) / len(query_terms), 4)

    @staticmethod
    def _search_terms(value: str) -> set[str]:
        normalized = value.lower()
        terms = set(re.findall(r"[a-z0-9_]{2,}", normalized))
        chinese_runs = re.findall(r"[\u4e00-\u9fff]+", normalized)
        for run in chinese_runs:
            if len(run) == 1:
                terms.add(run)
                continue
            terms.update(run[index : index + 2] for index in range(len(run) - 1))
        return terms

    def build_existing_memory_text(self, user_id: str, *, max_chars: int = 4000) -> str:
        # rejected/pending/sensitive candidates must not be echoed back to a model
        # merely for dedupe. Only already-approved active memories are provider input.
        memories = [
            memory
            for memory in self.repo.list_by_user(user_id, enabled_only=True)
            if getattr(memory, "sensitivity", "normal") != "sensitive"
        ]
        lines: list[str] = []
        total = 0
        for memory in memories:
            line = f"- [{memory.memory_type}] {memory.title}: {memory.content}"
            next_total = total + len(line) + (1 if lines else 0)
            if next_total > max_chars:
                continue
            lines.append(line)
            total = next_total
        return "\n".join(lines) or "无"

    @staticmethod
    def _similarity(left: str, right: str) -> float:
        return difflib.SequenceMatcher(None, left, right).ratio()

    @classmethod
    def enrich_suggestion_risks(
        cls,
        *,
        suggestions: list[MemorySuggestion],
        existing_memories: list[UserMemory],
    ) -> list[MemorySuggestion]:
        enriched: list[MemorySuggestion] = []
        for suggestion in suggestions:
            duplicate_memory_id = None
            conflict_memory_id = None
            risk_level = "safe"
            risk_reason = None

            suggestion_title = cls.normalize_text(suggestion.title).lower()
            suggestion_content = cls.normalize_text(suggestion.content).lower()
            content_risk, content_risk_reason = cls._candidate_content_risk(suggestion)
            for memory in existing_memories:
                if memory.memory_type != suggestion.memory_type:
                    continue
                memory_title = cls.normalize_text(memory.title).lower()
                memory_content = cls.normalize_text(memory.content).lower()
                content_similarity = cls._similarity(suggestion_content, memory_content)
                title_similarity = cls._similarity(suggestion_title, memory_title)

                if content_similarity >= 0.82:
                    duplicate_memory_id = memory.id
                    risk_level = "duplicate"
                    risk_reason = f"与已有记忆“{memory.title}”内容高度相似"
                    break
                if title_similarity >= 0.72 and content_similarity <= 0.55:
                    conflict_memory_id = memory.id
                    risk_level = "conflict"
                    risk_reason = f"与已有记忆“{memory.title}”标题相近但内容差异较大"
                    break

            # Duplicate means the candidate has no persistence value. Otherwise
            # sensitivity outranks semantic conflict because a conflict review
            # must never become a shortcut for activating a secret or identifier.
            if risk_level != "duplicate" and content_risk in {"sensitive", "volatile"}:
                risk_level = content_risk
                risk_reason = content_risk_reason
            elif risk_level == "safe" and content_risk == "review_required":
                risk_level = content_risk
                risk_reason = content_risk_reason

            enriched.append(
                suggestion.model_copy(
                    update={
                        "duplicate_memory_id": duplicate_memory_id,
                        "conflict_memory_id": conflict_memory_id,
                        "risk_level": risk_level,
                        "risk_reason": risk_reason,
                    }
                )
            )
        return enriched

    @classmethod
    def _candidate_content_risk(cls, suggestion: MemorySuggestion) -> tuple[str, str | None]:
        text = f"{cls.normalize_text(suggestion.title)}\n{cls.normalize_text(suggestion.content)}"
        if (
            cls.SENSITIVE_VALUE_PATTERN.search(text)
            or cls.EMAIL_PATTERN.search(text)
            or cls.PHONE_PATTERN.search(text)
            or cls.ID_CARD_PATTERN.search(text)
        ):
            return "sensitive", "候选中可能包含凭证或直接个人标识，不能自动激活"
        if cls.VOLATILE_TIME_PATTERN.search(text):
            return "volatile", "候选包含明显的短期时间表达，需确认有效期后再保存"
        if suggestion.memory_type == "instruction":
            return "review_required", "长期指令会持续影响后续回答，必须由用户确认"
        return "safe", None

    @classmethod
    def normalize_suggestions(
        cls,
        payload: Any,
        *,
        max_candidates: int,
        source_conversation_id: str | None = None,
        source_message_ids: str | None = None,
    ) -> list[MemorySuggestion]:
        if not isinstance(payload, list):
            return []

        suggestions: list[MemorySuggestion] = []
        seen: set[tuple[str, str]] = set()
        for item in payload:
            if not isinstance(item, dict):
                continue
            memory_type = cls.normalize_memory_type(item.get("memory_type"))
            title = cls.normalize_text(item.get("title"))[:120]
            content = cls.normalize_text(item.get("content"))
            reason = cls.normalize_text(item.get("reason"))
            if not title or not content:
                continue
            dedupe_key = (memory_type, content)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            suggestions.append(
                MemorySuggestion(
                    memory_type=memory_type,
                    title=title,
                    content=content,
                    reason=reason or None,
                    source_conversation_id=source_conversation_id,
                    source_message_ids=source_message_ids,
                    confidence=cls.normalize_text(item.get("confidence")) or "medium",
                )
            )
            if len(suggestions) >= max_candidates:
                break
        return suggestions

    @classmethod
    def parse_suggestion_json(
        cls,
        text: str,
        *,
        max_candidates: int,
        source_conversation_id: str | None = None,
        source_message_ids: str | None = None,
    ) -> list[MemorySuggestion]:
        normalized = text.strip()
        if not normalized:
            return []

        candidates = [normalized]
        fenced_match = re.search(r"```(?:json)?\s*(.*?)```", normalized, flags=re.DOTALL)
        if fenced_match:
            candidates.insert(0, fenced_match.group(1).strip())
        array_match = re.search(r"\[.*\]", normalized, flags=re.DOTALL)
        if array_match:
            candidates.insert(0, array_match.group(0).strip())

        for candidate in candidates:
            try:
                parsed = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            suggestions = cls.normalize_suggestions(
                parsed,
                max_candidates=max_candidates,
                source_conversation_id=source_conversation_id,
                source_message_ids=source_message_ids,
            )
            if suggestions:
                return suggestions
        return []
