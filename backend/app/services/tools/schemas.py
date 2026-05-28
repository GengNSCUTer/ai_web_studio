from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class ExternalSource:
    source_type: str
    provider: str
    title: str
    display_text: str
    url: str | None = None
    rank: int | None = None
    score: float | None = None
    used_in_prompt: bool = True
    citation_label: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ToolDefinition:
    tool_key: str
    provider: str
    category: str
    display_name: str
    description: str
    enabled_by_default: bool = True
    read_only: bool = True


@dataclass
class PlannedToolCall:
    call_id: str
    tool_key: str
    provider: str
    category: str
    display_name: str
    confidence: float
    reason: str
    arguments: dict[str, Any] = field(default_factory=dict)

    def to_public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ToolPlan:
    plan_id: str
    router: str
    external_context_allowed: bool
    should_use_tools: bool
    calls: list[PlannedToolCall]
    fallback_tool_key: str | None = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "router": self.router,
            "external_context_allowed": self.external_context_allowed,
            "should_use_tools": self.should_use_tools,
            "calls": [call.to_public_dict() for call in self.calls],
            "fallback_tool_key": self.fallback_tool_key,
        }


@dataclass
class ToolTraceEvent:
    type: str
    payload: dict[str, Any]

    def to_public_dict(self) -> dict[str, Any]:
        return {"type": self.type, **self.payload}


@dataclass
class ToolCallResult:
    call: PlannedToolCall
    status: str
    sources: list[ExternalSource]
    elapsed_ms: int
    error_message: str | None = None


@dataclass
class ExternalContextResult:
    context_text: str | None
    sources: list[ExternalSource]
    notices: list[str]
    diagnostics: dict[str, Any]
    details: dict[str, Any]
    tool_plan: ToolPlan | None = None
    tool_events: list[ToolTraceEvent] = field(default_factory=list)
