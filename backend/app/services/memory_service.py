import json
import re
from typing import Any

from app.models.user_memory import UserMemory
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

    def __init__(self, repo: UserMemoryRepository):
        self.repo = repo

    @classmethod
    def normalize_memory_type(cls, value: str | None) -> str:
        normalized = (value or "").strip() or "fact"
        if normalized not in cls.VALID_MEMORY_TYPES:
            return "fact"
        return normalized

    @staticmethod
    def normalize_text(value: str | None) -> str:
        return " ".join((value or "").split()).strip()

    def list_memories(self, user_id: str) -> list[UserMemoryResponse]:
        return [UserMemoryResponse.model_validate(item) for item in self.repo.list_by_user(user_id)]

    def create_memory(self, user_id: str, payload: UserMemoryCreate) -> UserMemoryResponse:
        memory = UserMemory(
            user_id=user_id,
            memory_type=self.normalize_memory_type(payload.memory_type),
            title=self.normalize_text(payload.title)[:120] or "未命名记忆",
            content=self.normalize_text(payload.content),
            source="manual",
            is_enabled=payload.is_enabled,
        )
        saved = self.repo.save(memory)
        return UserMemoryResponse.model_validate(saved)

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

        saved = self.repo.save(memory)
        return UserMemoryResponse.model_validate(saved)

    def build_memory_context(self, user_id: str, *, max_chars: int) -> tuple[str | None, int, int]:
        memories = self.repo.list_by_user(user_id, enabled_only=True)
        if not memories:
            return None, 0, 0

        chunks: list[str] = []
        total_chars = 0
        limit = max(500, min(max_chars, 20000))

        for memory in memories:
            title = self.normalize_text(memory.title)
            content = self.normalize_text(memory.content)
            if not content:
                continue
            label = self.TYPE_LABELS.get(memory.memory_type, "长期记忆")
            line = f"- [{label}] {title}: {content}"
            next_total = total_chars + len(line) + 1
            if next_total > limit:
                break
            chunks.append(line)
            total_chars = next_total

        if not chunks:
            return None, len(memories), 0

        context = "以下是用户显式保存的长期记忆，请在不违背当前对话的前提下参考：\n" + "\n".join(chunks)
        return context, len(memories), len(context)

    def build_existing_memory_text(self, user_id: str, *, max_chars: int = 4000) -> str:
        memories = self.repo.list_by_user(user_id)
        lines: list[str] = []
        total = 0
        for memory in memories:
            line = f"- [{memory.memory_type}] {memory.title}: {memory.content}"
            if total + len(line) > max_chars:
                break
            lines.append(line)
            total += len(line)
        return "\n".join(lines) or "无"

    @classmethod
    def normalize_suggestions(cls, payload: Any, *, max_candidates: int) -> list[MemorySuggestion]:
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
                )
            )
            if len(suggestions) >= max_candidates:
                break
        return suggestions

    @classmethod
    def parse_suggestion_json(cls, text: str, *, max_candidates: int) -> list[MemorySuggestion]:
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
            suggestions = cls.normalize_suggestions(parsed, max_candidates=max_candidates)
            if suggestions:
                return suggestions
        return []
