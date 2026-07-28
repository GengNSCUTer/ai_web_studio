from __future__ import annotations

from dataclasses import asdict, dataclass, field
import re
from typing import Any


SENSITIVE_ARGUMENT_PATTERN = re.compile(
    r"(^|[_-])(api[_-]?key|token|password|passwd|secret|credential|authorization|auth)([_-]|$)",
    flags=re.IGNORECASE,
)


def redact_sensitive_arguments(value: Any) -> Any:
    """Redact model/tool arguments before Trace, API responses and persistence."""
    if isinstance(value, dict):
        return {
            str(key): "***" if SENSITIVE_ARGUMENT_PATTERN.search(str(key)) else redact_sensitive_arguments(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive_arguments(item) for item in value]
    return value


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
    when_to_use: list[str] = field(default_factory=list)
    when_not_to_use: list[str] = field(default_factory=list)
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    adapter_type: str = "python"
    adapter: dict[str, Any] = field(default_factory=dict)
    source_type: str = "local_manifest"
    risk_level: str = "low"
    fallback_tool_key: str | None = None
    enabled_by_default: bool = True
    read_only: bool = True

    @property
    def credential_provider(self) -> str:
        return str(self.adapter.get("credential_provider") or self.provider)

    @property
    def credential_required(self) -> bool:
        auth_type = str(self.adapter.get("auth_type") or "api_key").strip() or "api_key"
        endpoint_template = str(self.adapter.get("endpoint_template") or "")
        return auth_type != "none" or "{api_key}" in endpoint_template

    def to_public_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ToolResultBinding:
    source_call_id: str
    source_path: str
    target_argument: str
    required: bool = True


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
    depends_on: list[str] = field(default_factory=list)
    can_parallel: bool = True
    result_bindings: list[ToolResultBinding] = field(default_factory=list)

    def to_public_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["arguments"] = redact_sensitive_arguments(self.arguments)
        return payload


@dataclass
class ToolPlan:
    plan_id: str
    router: str
    external_context_allowed: bool
    should_use_tools: bool
    calls: list[PlannedToolCall]
    fallback_tool_key: str | None = None
    original_query: str | None = None
    rewritten_query: str | None = None
    need_more_rounds: bool = False
    trace_events: list[dict[str, Any]] = field(default_factory=list)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "router": self.router,
            "external_context_allowed": self.external_context_allowed,
            "should_use_tools": self.should_use_tools,
            "calls": [call.to_public_dict() for call in self.calls],
            "fallback_tool_key": self.fallback_tool_key,
            "original_query": self.original_query,
            "rewritten_query": self.rewritten_query,
            "need_more_rounds": self.need_more_rounds,
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
