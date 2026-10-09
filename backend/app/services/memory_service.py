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
from app.services.memory_policy import contains_credential, equivalent_content, lifetime, memory_identity, requested_response_language
from app.services.memory_extraction_policy import redact_source


@dataclass(frozen=True)
class MemoryContextSelection:
    """本轮选中的记忆；检索与上下文组装分开，便于后续引入语义召回。"""

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
    EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", flags=re.IGNORECASE)
    PHONE_PATTERN = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
    ID_CARD_PATTERN = re.compile(r"(?<!\d)\d{17}[0-9Xx](?!\d)")

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
        with self.repo.mutation(user_id):
            project_id = self._resolve_source_scope(user_id, payload.source_conversation_id, payload.project_id)
            memory = UserMemory(
                user_id=user_id, memory_type=self.normalize_memory_type(payload.memory_type),
                title=self.normalize_text(payload.title)[:120] or "未命名记忆",
                content=self.normalize_text(payload.content), source="manual", status="active",
                source_conversation_id=payload.source_conversation_id,
                source_message_ids=payload.source_message_ids, confidence=payload.confidence,
                is_enabled=payload.is_enabled, project_id=project_id,
                expires_at=self._normalize_expiry(payload.expires_at), version=1,
            )
            self._validate_persistence(memory)
            self._validate_activation(memory, payload.supersedes_memory_id)
            self._set_identity(memory)
            return self._memory_response(self.repo.save(memory), user_id)

    def _resolve_source_scope(self, user_id: str, conversation_id: str | None, project_id: str | None) -> str | None:
        if conversation_id:
            conversation = self.conversation_repo.get_by_user(conversation_id, user_id) if self.conversation_repo else None
            if not conversation:
                raise ValueError("来源会话不存在或无权访问")
            source_project = getattr(conversation, "project_id", None)
            if project_id is not None and project_id != source_project:
                raise ValueError("项目范围与来源会话不一致")
            project_id = source_project
        if project_id and not self.repo.owns_project(user_id, project_id):
            raise ValueError("项目不存在或无权访问")
        return project_id

    @classmethod
    def _validate_persistence(cls, memory: UserMemory) -> None:
        if contains_credential(memory.title, memory.content):
            raise ValueError("密码、密钥和 Token 不能保存为普通长期记忆")
        if not cls.normalize_text(memory.content):
            raise ValueError("记忆内容不能为空")
        expiry = cls._normalize_expiry(memory.expires_at)
        if expiry is not None and expiry <= datetime.now(timezone.utc):
            raise ValueError("expires_at 必须晚于当前时间")
        time_kind = lifetime(memory.title, memory.content)
        if time_kind == "turn":
            raise ValueError("本轮要求只作用于当前对话，不保存为长期记忆")
        if time_kind == "temporary" and expiry is None:
            raise ValueError("短期候选必须设置 expires_at")

    @classmethod
    def _set_identity(cls, memory: UserMemory) -> None:
        identity = memory_identity(memory.memory_type, memory.title, memory.content)
        memory.fact_key, memory.fact_value = identity.key, identity.value
        memory.content_hash = cls.memory_content_hash(user_id=memory.user_id, memory_type=memory.memory_type,
                                                      content=memory.content, project_id=memory.project_id)

    def _memory_scope(self, memory: UserMemory, user_id: str) -> str | None:
        explicit = getattr(memory, "project_id", None)
        if explicit:
            return explicit
        source = getattr(memory, "source_conversation_id", None)
        if not source:
            return None
        conversation = self.conversation_repo.get_by_user(source, user_id) if self.conversation_repo else None
        return getattr(conversation, "project_id", None) if conversation else "__unresolved__"

    def _validate_activation(self, memory: UserMemory, target_id: str | None = None) -> None:
        """锁内按最新 active 版本重新判断，不能信任提取时的旧风险标签。"""
        scope = self._memory_scope(memory, memory.user_id)
        if scope == "__unresolved__":
            raise ValueError("来源会话无法解析，不能激活记忆")
        memory.project_id = scope
        existing = [item for item in self.repo.list_others(memory.user_id, memory.id)
                    if item.id != memory.id and item.status == "active"
                    and (item.expires_at is None or self._normalize_expiry(item.expires_at) > datetime.now(timezone.utc))
                    and self._memory_scope(item, memory.user_id) == scope]
        suggestion = MemorySuggestion(memory_type=memory.memory_type, title=memory.title,
                                      content=memory.content, project_id=scope)
        # 已限定范围的旧行按当前解析范围比较，包括升级前只带来源会话的记录。
        current = self.enrich_suggestion_risks(suggestions=[suggestion], existing_memories=existing,
                                             scoped=True)[0]
        if current.duplicate_memory_id:
            raise ValueError("重复候选没有激活价值，请使用已有记忆")
        time_kind = lifetime(memory.title, memory.content)
        if time_kind != "durable" and target_id:
            raise ValueError("短期记忆不能替换长期版本")
        # 有效期明确的短期事实可并存，不销毁长期基线，过期后自然恢复基线。
        if current.conflict_memory_id and time_kind == "durable" and not target_id:
            raise ValueError("冲突候选必须明确 supersedes_memory_id 才能确认")
        if target_id:
            previous = self.repo.get_by_user(target_id, memory.user_id)
            if not previous or previous.id == memory.id or previous.status != "active":
                raise ValueError("要替换的旧记忆不存在或不是 active 版本")
            if self._memory_scope(previous, memory.user_id) != scope:
                raise ValueError("不能替换其他项目范围的记忆")
            old_identity = memory_identity(previous.memory_type, previous.title, previous.content)
            new_identity = memory_identity(memory.memory_type, memory.title, memory.content)
            if old_identity.key != new_identity.key:
                raise ValueError("只能替换同一事实的旧版本")
            if current.conflict_memory_id and current.conflict_memory_id != target_id:
                raise ValueError("冲突目标已变化，请重新审核")
            previous.status, previous.is_enabled = "superseded", False
            previous.version += 1
            self.repo.flush(previous)
            memory.supersedes_memory_id = previous.id

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
        supersedes_memory_id: str | None = None,
        expected_version: int | None = None,
    ) -> UserMemoryResponse:
        with self.repo.mutation(memory.user_id):
            memory = self.repo.fresh(memory, expected_version, idempotent_status="active")
            if memory.status == "active":
                self._validate_persistence(memory)
                return self._memory_response(memory, memory.user_id)
            if memory.status != "pending":
                raise ValueError("只有 pending 候选可以确认")
            if expires_at is not None:
                memory.expires_at = self._normalize_expiry(expires_at)
            self._validate_persistence(memory)
            target = supersedes_memory_id or memory.supersedes_memory_id
            # 旧标签保留审核意义；即使旧冲突目标已失效也不能静默变成新增。
            if memory.risk_level == "conflict" and not target:
                raise ValueError("冲突候选必须明确 supersedes_memory_id 才能确认")
            self._validate_activation(memory, target)
            self._set_identity(memory)
            memory.status, memory.is_enabled = "active", True
            memory.review_at = datetime.now(timezone.utc)
            memory.version += 1
            return self._memory_response(self.repo.save(memory), memory.user_id)

    def revoke_memory(self, *, memory: UserMemory) -> UserMemoryResponse:
        return self._close_memory(memory, "revoked", {"active", "pending"})

    def reject_candidate(self, *, memory: UserMemory) -> UserMemoryResponse:
        return self._close_memory(memory, "rejected", {"pending"})

    def _close_memory(self, memory: UserMemory, status: str, allowed: set[str]) -> UserMemoryResponse:
        with self.repo.mutation(memory.user_id):
            memory = self.repo.fresh(memory, idempotent_status=status)
            if memory.status == status:
                return self._memory_response(memory, memory.user_id)
            if memory.status not in allowed:
                raise ValueError("当前记忆状态不能执行该操作")
            memory.status, memory.is_enabled = status, False
            memory.review_at = datetime.now(timezone.utc)
            memory.version += 1
            return self._memory_response(self.repo.save(memory), memory.user_id)

    def update_memory(
        self,
        *,
        memory: UserMemory,
        payload: UserMemoryUpdate,
    ) -> UserMemoryResponse:
        with self.repo.mutation(memory.user_id):
            memory = self.repo.fresh(memory, payload.expected_version)
            data = payload.model_dump(exclude_unset=True, exclude={"expected_version"})
            if memory.status != "active" and data.get("is_enabled"):
                raise ValueError("非 active 记忆不能直接启用，请使用候选审核接口")
            if memory.status not in {"active", "pending"}:
                raise ValueError("历史或撤销的记忆不能修改")
            values = {field: getattr(memory, field) for field in (
                "user_id", "memory_type", "title", "content", "source", "source_conversation_id",
                "source_message_ids", "confidence", "is_enabled", "status", "project_id", "expires_at",
                "importance", "sensitivity", "risk_level", "candidate_reason")}
            for field, value in data.items():
                if field == "expires_at":
                    values[field] = self._normalize_expiry(value)
                elif field == "memory_type":
                    values[field] = self.normalize_memory_type(value)
                elif value is not None or field in {"source_conversation_id", "source_message_ids", "confidence"}:
                    values[field] = self.normalize_text(value) if isinstance(value, str) else value
            if "source_conversation_id" in data:
                scope = self._resolve_source_scope(memory.user_id, values["source_conversation_id"], memory.project_id)
                if scope != self._memory_scope(memory, memory.user_id):
                    raise ValueError("不能通过修改来源改变记忆的项目范围")
            values["title"] = values["title"][:120] or memory.title
            proposed = UserMemory(**values, version=memory.version + 1)
            self._validate_persistence(proposed)
            changed = any(getattr(proposed, field) != getattr(memory, field) for field in ("title", "content", "memory_type"))
            if memory.status == "active" and changed:
                if lifetime(proposed.title, proposed.content) != "durable":
                    raise ValueError("短期记忆不能替换长期版本，请单独创建并设置有效期")
                # 编辑已生效内容产生后继版本，旧内容和来源保留，状态切换与新行一起提交。
                others = [item for item in self.repo.list_by_user(memory.user_id)
                          if item.id != memory.id and item.status == "active"
                          and self._memory_scope(item, memory.user_id) == self._memory_scope(memory, memory.user_id)]
                risk = self.enrich_suggestion_risks(suggestions=[MemorySuggestion(
                    memory_type=proposed.memory_type, title=proposed.title, content=proposed.content)],
                    existing_memories=others, scoped=True)[0]
                if risk.duplicate_memory_id or risk.conflict_memory_id:
                    raise ValueError("修改后的事实与其他 active 记忆重复或冲突，请先处理冲突")
                proposed.supersedes_memory_id = memory.id
                # 用户手动更正的正文不再冒用旧的自动核验证据或来源标签。
                proposed.source = "manual"
                proposed.evidence_quote = None
                memory.status, memory.is_enabled = "superseded", False
                memory.version += 1
                self.repo.flush(memory)
                target = proposed
            else:
                for field, value in values.items():
                    setattr(memory, field, value)
                memory.version += 1
                target = memory
            self._set_identity(target)
            return self._memory_response(self.repo.save(target), target.user_id)

    @staticmethod
    def _normalize_expiry(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    def select_memories_for_query(
        self,
        user_id: str,
        *,
        query: str | None,
        project_id: str | None = None,
        max_memories: int = 8,
        semantic_scores: dict[str, float] | None = None,
        ranking_scores: dict[str, float] | None = None,
        candidate_memories: list[UserMemory] | None = None,
    ) -> MemoryContextSelection:
        expire_due = getattr(self.repo, "expire_due", None)
        if expire_due and candidate_memories is None:
            expire_due(user_id)
        scoped_reader = getattr(self.repo, "list_for_retrieval", None)
        memories = candidate_memories if candidate_memories is not None else (
            scoped_reader(user_id, project_id) if scoped_reader else self.repo.list_by_user(user_id, enabled_only=True))
        language_override = requested_response_language(query)
        memories = [
            memory
            for memory in memories
            if self._memory_matches_project_scope(memory=memory, user_id=user_id, project_id=project_id)
            and not contains_credential(memory.title, memory.content)
            and getattr(memory, "sensitivity", "normal") != "sensitive"
            and not (language_override and memory_identity(memory.memory_type, memory.title, memory.content).key == "response_language")
        ]
        # 项目事实覆盖同项全局默认；有有效期的临时值只覆盖本轮选择，不修改基线。
        current: dict[str, UserMemory] = {}
        for item in memories:
            key = memory_identity(item.memory_type, item.title, item.content).key
            previous = current.get(key)
            def priority(value: UserMemory) -> tuple[bool, bool, float]:
                timestamp = getattr(value, "updated_at", None) or getattr(value, "created_at", None)
                recency = self._normalize_expiry(timestamp).timestamp() if isinstance(timestamp, datetime) else 0.0
                return (self._memory_scope(value, user_id) is not None,
                        lifetime(value.title, value.content) == "temporary", recency)
            if previous is None or priority(item) > priority(previous):
                current[key] = item
        memories = list(current.values())
        max_memories = max(0, min(max_memories, 32))
        if not memories:
            return MemoryContextSelection(memories=[], relevant_count=0, always_on_count=0)

        # 无问题的历史调用仍按最近更新时间选择；Chat 总是提供当前问题。
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
            relevance = self._query_relevance_score(normalized_query, f"{title}\n{content}")
            # 语义相似度只是召回信号，不能覆盖范围、状态、版本或本轮明确要求。
            semantic = (semantic_scores or {}).get(getattr(memory, "id", ""), 0.0)
            if semantic >= 0.45:
                relevance = max(relevance, semantic)
            score = self._memory_rank_score(memory, (ranking_scores or {}).get(getattr(memory, "id", ""), relevance))
            if memory.memory_type in {"instruction", "profile"}:
                always_on.append((memory, score))
            elif relevance > 0:
                relevant.append((memory, score))

        # 少量偏好常驻，其余名额按相关性选择，不能让偏好占满事实名额。
        always_on.sort(key=lambda item: item[1], reverse=True)
        relevant.sort(key=lambda item: item[1], reverse=True)
        preferences = always_on[:min(2, max(0, max_memories - (1 if relevant else 0)))]
        facts = relevant[:max_memories - len(preferences)]
        selected = [memory for memory, _ in [*preferences, *facts]]
        return MemoryContextSelection(
            memories=selected,
            relevant_count=sum(1 for memory, _ in relevant if memory in selected),
            always_on_count=sum(1 for memory, _ in always_on if memory in selected),
        )

    @classmethod
    def _memory_rank_score(cls, memory: UserMemory, relevance: float) -> float:
        """相关性为主，有限的重要度和新鲜度仅作排序补充。"""

        try:
            importance = float(getattr(memory, "importance", 0.5) or 0.5)
        except (TypeError, ValueError):
            importance = 0.5
        importance = max(0.0, min(1.0, importance))
        updated_at = getattr(memory, "updated_at", None) or getattr(memory, "created_at", None)
        freshness = 0.5
        if isinstance(updated_at, datetime):
            timestamp = updated_at
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)
            age_days = max(0.0, (datetime.now(timezone.utc) - timestamp).total_seconds() / 86400)
            freshness = 1.0 / (1.0 + age_days / 30.0)
        return round((0.75 * relevance) + (0.15 * importance) + (0.10 * freshness), 6)

    def build_memory_context(
        self,
        user_id: str,
        *,
        max_chars: int,
        query: str | None = None,
        project_id: str | None = None,
    ) -> tuple[str | None, int, int]:
        selection = self.select_memories_for_query(user_id, query=query, project_id=project_id)
        context, records = self.render_selection(selection.memories, max_chars=max_chars)
        return context, len(records), len(context or "")

    @classmethod
    def render_selection(cls, memories: list[UserMemory], *, max_chars: int) -> tuple[str | None, list[dict[str, Any]]]:
        """保留整条事实；返回逐条行文本，供总预算治理后核对实际注入。"""
        if not memories:
            return None, []

        header = "以下是已启用的长期记忆，请参考；若与本轮用户明确要求冲突，以本轮要求为准："
        chunks: list[str] = []
        records: list[dict[str, Any]] = []
        total_chars = len(header) + 1
        limit = max(500, min(max_chars, 20000))

        preference_chars = 0
        has_facts = any(item.memory_type not in {"profile", "instruction"} for item in memories)
        for memory in memories:
            title = cls.normalize_text(memory.title)
            content = cls.normalize_text(memory.content)
            if not content:
                continue
            label = cls.TYPE_LABELS.get(memory.memory_type, "长期记忆")
            line = f"- [{label}] {title}: {content}"
            if has_facts and memory.memory_type in {"profile", "instruction"}:
                # 除数量外也限制字符，避免一条长偏好吞掉所有事实输入空间。
                if preference_chars + len(line) + 1 > limit // 4:
                    continue
                preference_chars += len(line) + 1
            next_total = total_chars + len(line) + (1 if chunks else 0)
            if next_total > limit:
                # 单条异常长记忆不能阻塞后续所有短记忆；也不截断事实，避免把半句话注入模型。
                continue
            chunks.append(line)
            records.append({"id": getattr(memory, "id", None), "type": memory.memory_type,
                            "project_id": getattr(memory, "project_id", None),
                            "version": getattr(memory, "version", 1), "line": line})
            total_chars = next_total

        if not chunks:
            return None, []

        context = header + "\n" + "\n".join(chunks)
        return context, records

    def _memory_matches_project_scope(
        self,
        *,
        memory: UserMemory,
        user_id: str,
        project_id: str | None,
    ) -> bool:
        """四种记忆都遵守同一范围规则；无来源的手工记忆仍可作为用户全局画像。"""
        scope = self._memory_scope(memory, user_id)
        return scope is None or scope == project_id

    @classmethod
    def _query_relevance_score(cls, query: str, candidate: str) -> float:
        """中文双字和英文标识符的确定性词法分数；语义由独立召回服务补充。"""

        query_terms = cls._search_terms(query)
        candidate_terms = cls._search_terms(candidate)
        if not query_terms or not candidate_terms:
            return 0.0
        overlap = query_terms & candidate_terms
        if not overlap:
            return 0.0
        # 按问题词数归一，避免长记忆仅因包含大量泛词就获得高分。
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

    def build_existing_memory_text(self, user_id: str, *, max_chars: int = 4000, project_id: str | None = None) -> str:
        # 去重辅助输入同样过滤范围和凭证，旧数据库中的危险内容也不能回传模型。
        memories = [
            memory
            for memory in self.repo.list_by_user(user_id, enabled_only=True)
            if getattr(memory, "sensitivity", "normal") != "sensitive"
            and not contains_credential(memory.title, memory.content)
            and self._memory_matches_project_scope(memory=memory, user_id=user_id, project_id=project_id)
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

    @classmethod
    def enrich_suggestion_risks(
        cls,
        *,
        suggestions: list[MemorySuggestion],
        existing_memories: list[UserMemory],
        scoped: bool = False,
    ) -> list[MemorySuggestion]:
        enriched: list[MemorySuggestion] = []
        for suggestion in suggestions:
            duplicate_memory_id = None
            conflict_memory_id = None
            risk_level = "safe"
            risk_reason = None

            identity = memory_identity(suggestion.memory_type, suggestion.title, suggestion.content)
            content_risk, content_risk_reason = cls._candidate_content_risk(suggestion)
            for memory in existing_memories:
                if getattr(memory, "status", "active") != "active":
                    continue
                if not scoped and getattr(memory, "project_id", None) != suggestion.project_id:
                    continue
                expiry = cls._normalize_expiry(getattr(memory, "expires_at", None))
                if expiry is not None and expiry <= datetime.now(timezone.utc):
                    continue
                old = memory_identity(memory.memory_type, memory.title, memory.content)
                same_key = identity.key == old.key
                duplicate = (same_key and identity.structured and old.structured and identity.value == old.value)
                allow_reordering = bool(re.search(r"技术栈|技术基础", suggestion.title))
                duplicate |= (same_key and equivalent_content(suggestion.content, memory.content,
                                                              allow_reordering=allow_reordering))
                # 临时状态与长期基线不能因为相似而互相替代。
                duplicate &= lifetime(suggestion.title, suggestion.content) == lifetime(memory.title, memory.content)
                if duplicate:
                    duplicate_memory_id = memory.id
                    risk_level = "duplicate"
                    risk_reason = "与当前范围内已有记忆表达同一事实和同一值"
                    break
                if same_key and lifetime(suggestion.title, suggestion.content) == "durable" and lifetime(memory.title, memory.content) == "durable":
                    conflict_memory_id = memory.id
                    risk_level = "conflict"
                    risk_reason = "同一事实的值有变化或含歧义，需要审核后替换旧版本"
                    break

            if content_risk == "sensitive" or (risk_level != "duplicate" and content_risk == "volatile"):
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
            contains_credential(suggestion.title, suggestion.content)
            or cls.EMAIL_PATTERN.search(text)
            or cls.PHONE_PATTERN.search(text)
            or cls.ID_CARD_PATTERN.search(text)
        ):
            return "sensitive", "候选中可能包含凭证或直接个人标识，不能自动激活"
        if lifetime(suggestion.title, suggestion.content) != "durable":
            return "volatile", "候选包含明显的短期时间表达，需确认有效期后再保存"
        if suggestion.memory_type == "instruction":
            return "review_required", "长期指令会持续影响后续回答，必须由用户确认"
        if suggestion.confidence == "low":
            return "review_required", "模型置信度较低，只能作为人工审核候选"
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
            reason = redact_source(cls.normalize_text(item.get("reason")))
            if not title or not content:
                continue
            if contains_credential(title, content):
                # 生成器的输出也不允许把凭证作为候选落库或返回给用户复用。
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
                    source_message_id=cls.normalize_text(item.get("source_message_id"))[:36] or None,
                    evidence_quote=cls.normalize_text(item.get("evidence_quote"))[:1200] or None,
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
