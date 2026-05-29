from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.repositories.conversation_repo import ConversationRepository
from app.services.context_governance_service import ContextGovernanceService
from app.services.message_service import MessageService
from app.services.tokenizer_service import TokenizerEstimator


@dataclass
class ChatExecutionContext:
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


@dataclass
class ExistingTurnExecutionInput:
    conversation: object
    history_rows: list[object]
    user_message: object
    assistant_message: object
    model_name: str | None
    system_prompt: str | None
    thinking_enabled: bool
    thinking_budget: int | None
    web_search_enabled: bool


@dataclass
class ChatRuntimeConfig:
    settings: object
    provider_api_key: str | None
    resolved_model: str
    provider_type: str
    base_url: str
    budget: object
    tokenizer: TokenizerEstimator
    governance_service: ContextGovernanceService


@dataclass
class MemoryContextBundle:
    context_text: str | None
    count: int
    chars: int


@dataclass
class SummaryRefreshBundle:
    summary: str | None
    boundary_message_id: str | None
    stats: dict[str, Any]


@dataclass
class PromptDiagnosticsBundle:
    prompt_prefix_hash: str
    prompt_prefix_tokens: int
    prompt_total_tokens: int
    prompt_recent_history_tokens: int
    prompt_prefix_reused_last_turn: int
