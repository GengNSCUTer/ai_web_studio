import difflib
import json
import re
from typing import Any

from app.models.user_memory import UserMemory
from app.repositories.conversation_repo import ConversationRepository
from app.repositories.memory_repo import UserMemoryRepository
from app.schemas.memory import MemorySuggestion, UserMemoryCreate, UserMemoryResponse, UserMemoryUpdate


class MemoryService:
    VALID_MEMORY_TYPES = {"profile", "project", "fact", "instruction"}
    TYPE_LABELS = {
        "profile": "用户偏好",
        "project": "项目背景",
        "fact": "重要事实",
        "instruction": "长期指令",
    }

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
        return [self._memory_response(item, user_id) for item in self.repo.list_by_user(user_id)]

    def create_memory(self, user_id: str, payload: UserMemoryCreate) -> UserMemoryResponse:
        memory = UserMemory(
            user_id=user_id,
            memory_type=self.normalize_memory_type(payload.memory_type),
            title=self.normalize_text(payload.title)[:120] or "未命名记忆",
            content=self.normalize_text(payload.content),
            source="manual",
            source_conversation_id=self.normalize_text(payload.source_conversation_id) or None,
            source_message_ids=self.normalize_text(payload.source_message_ids) or None,
            confidence=self.normalize_text(payload.confidence) or None,
            is_enabled=payload.is_enabled,
        )
        saved = self.repo.save(memory)
        return self._memory_response(saved, user_id)

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
            memory.is_enabled = data["is_enabled"]
        if "source_conversation_id" in data:
            memory.source_conversation_id = self.normalize_text(data["source_conversation_id"]) or None
        if "source_message_ids" in data:
            memory.source_message_ids = self.normalize_text(data["source_message_ids"]) or None
        if "confidence" in data:
            memory.confidence = self.normalize_text(data["confidence"]) or None

        saved = self.repo.save(memory)
        return self._memory_response(saved, memory.user_id)

    def build_memory_context(self, user_id: str, *, max_chars: int) -> tuple[str | None, int, int]:
        memories = self.repo.list_by_user(user_id, enabled_only=True)
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

    def build_existing_memory_text(self, user_id: str, *, max_chars: int = 4000) -> str:
        memories = self.repo.list_by_user(user_id)
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
