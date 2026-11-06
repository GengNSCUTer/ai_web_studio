"""一次 Chat 上下文组装的请求级中间结果。

这些对象只存在于当前请求的 Runtime Context，不进入 LangGraph State，也不写入
Checkpointer。它们可以包含证据对象和 ORM 外的值副本，但不能包含 Session 凭据。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChatAssemblyRuntime:
    conversation: Any
    history_rows: list[Any]
    user_message: Any
    assistant_message: Any
    runtime: Any
    thinking_enabled: bool
    thinking_budget: int | None
    web_search_enabled: bool
    knowledge_base_id: str | None = None
    knowledge_base_ids: list[str] | None = None
    skill_context: Any = None
    tool_run_mode: str = "quick_chat"
    query: str = ""
    resource_selections: list[dict[str, Any]] = field(default_factory=list)
    assistant_generation_id: str | None = None
    assistant_id: str | None = None
    bounded_history: bool = False
    history_through_message_id: str | None = None
    history_snapshot: list[tuple[Any, ...]] = field(default_factory=list)
    memory_bundle: Any = None
    attachment_context_result: Any = None
    resource_sources: list[Any] = field(default_factory=list)
    resource_notices: list[str] = field(default_factory=list)
    resource_service: Any = None
    external_context_result: Any = None
    evidence_sufficiency: Any = None
    knowledge_context_result: Any = None
    retrieval_log_ids: list[str] = field(default_factory=list)
    summary_bundle: Any = None
    prompt_result: Any = None
    governed_context: Any = None
    memory_diagnostics: dict[str, Any] = field(default_factory=dict)
    prompt_diagnostics: Any = None
    result: Any = None
    phases: list[str] = field(default_factory=list)

