"""Chat 图的状态和请求级运行时对象。

图状态只保存可序列化的阶段诊断；数据库 Session、认证用户、Provider 配置和 ORM
对象都放在 Runtime Context 中，避免后续接入 Checkpointer 时把敏感信息持久化。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, TypedDict


class ChatGraphState(TypedDict, total=False):
    """Chat 准备图的可观察状态。"""

    phase: str
    turn_kind: Literal["new", "existing"]
    conversation_id: str
    user_message_id: str
    assistant_message_id: str
    generation_id: str | None
    context_ready: bool
    history_count: int
    source_count: int
    tool_event_count: int
    context_token_count: int
    terminal_reason: str
    final_source_labels: list[str]


@dataclass
class ChatGraphContext:
    """一次请求独占的 Chat 运行时对象，不写入图 State。"""

    service: Any
    turn_kind: Literal["new", "existing"]
    payload: Any = None
    execution_input: Any = None
    default_settings: Any = None
    turn: Any = None
    conversation: Any = None
    user_message: Any = None
    assistant_message: Any = None
    # 在第一次提交后立即复制，后续 rollback/refresh 不能重新从过期 ORM 对象读取。
    generation_id: str | None = None
    history_rows: list[Any] | None = None
    runtime: Any = None
    skill_context: Any = None
    result: Any = None
    phase_log: list[str] | None = None
