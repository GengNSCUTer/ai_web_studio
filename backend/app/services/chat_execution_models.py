from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.repositories.conversation_repo import ConversationRepository
from app.services.context_governance_service import ContextGovernanceService
from app.services.message_service import MessageService
from app.services.tokenizer_service import TokenizerEstimator


@dataclass
class ChatExecutionContext:
    """模型流式执行所需的完整上下文。

    这是 prepare 阶段和 streaming 阶段之间的交接对象：
    prepare阶段填好conversation/message/prompt/provider/diagnostics；
    独立Chat Worker消费它调用生成服务并保存结果，HTTP仅订阅已持久化事件。
    """

    conversation_repo: ConversationRepository
    message_service: MessageService
    conversation: object
    user_message: object
    assistant_message: object
    history_messages: list[dict[str, Any]]
    resolved_model: str
    provider_type: str
    base_url: str
    api_key: str | None
    temperature: float
    top_p: float
    max_tokens: int | None
    context_notices: list[str]
    context_stats: dict[str, Any]
    context_details: dict[str, Any]
    context_summary: str | None
    thinking_enabled: bool
    thinking_budget: int | None
    tool_events: list[dict[str, Any]]
    external_sources: list[dict[str, Any]]
    prompt_cache_key: str | None = None
    prompt_cache_breakpoint: int = 0
    # Captured before external work; it must not be re-read from an expired ORM object
    # while closing a failed or cancelled request.
    generation_id: str | None = None
    # 专门编辑模式直接展示真实执行状态，避免最终模型虚构审批或写入成功。
    verified_file_edit_answer: str | None = None


@dataclass
class ExistingTurnExecutionInput:
    """重生成/编辑重答的输入。

    与新问题不同，这类请求不创建新的 user/assistant 消息，而是复用已有最后一轮消息。
    """

    conversation: object
    history_rows: list[object]
    user_message: object
    assistant_message: object
    model_name: str | None
    system_prompt: str | None
    thinking_enabled: bool
    thinking_budget: int | None
    web_search_enabled: bool
    knowledge_base_id: str | None = None
    knowledge_base_ids: list[str] | None = None
    skill_key: str | None = None
    tool_run_mode: str = "quick_chat"


@dataclass
class ChatRuntimeConfig:
    """一次模型调用的运行时配置。

    它来自“用户设置 + 会话覆盖 + 上下文预算计算”，不包含具体消息内容。
    """

    settings: object
    provider_api_key: str | None
    resolved_model: str
    provider_type: str
    base_url: str
    budget: object
    tokenizer: TokenizerEstimator
    governance_service: ContextGovernanceService


@dataclass
class SummaryRefreshBundle:
    """滚动摘要刷新结果。"""

    summary: str | None
    boundary_message_id: str | None
    stats: dict[str, Any]


@dataclass
class PromptDiagnosticsBundle:
    """prompt 观测数据，用于上下文面板和 prefix cache 命中分析。"""

    prompt_prefix_hash: str
    prompt_prefix_tokens: int
    prompt_total_tokens: int
    prompt_recent_history_tokens: int
    prompt_prefix_reused_last_turn: int
