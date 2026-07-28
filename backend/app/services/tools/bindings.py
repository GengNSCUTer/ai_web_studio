from __future__ import annotations

from dataclasses import replace
import math
import re
from typing import Any

from app.services.tools.schemas import (
    ExternalSource,
    PlannedToolCall,
    ToolDefinition,
    ToolResultBinding,
    ToolTraceEvent,
)
from app.services.tools.validation import ToolSchemaValidationError, ToolSchemaValidator


class ToolResultBindingError(ValueError):
    pass


class ToolResultBindingResolver:
    """Bind declared upstream structured values to downstream top-level arguments.

    Only JSON Pointer paths below ``/sources/<index>/metadata/raw`` are accepted.
    Display text and arbitrary expressions are intentionally excluded because tool
    output is untrusted model context rather than executable instructions.
    """

    TARGET_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]{0,63}$")
    SOURCE_PATH_PATTERN = re.compile(r"^/sources/\d+/metadata/raw(?:/.*)?$")
    MAX_BINDINGS = 8
    MAX_PATH_CHARS = 256
    MAX_STRING_CHARS = 1024
    MAX_ARRAY_ITEMS = 20

    def __init__(self, *, validator: ToolSchemaValidator | None = None) -> None:
        self.validator = validator or ToolSchemaValidator()

    @classmethod
    def validate_declaration(cls, binding: ToolResultBinding) -> None:
        if not cls.TARGET_PATTERN.fullmatch(binding.target_argument):
            raise ToolResultBindingError("绑定目标参数名不合法。")
        if not binding.source_call_id or len(binding.source_call_id) > 64:
            raise ToolResultBindingError("绑定来源 call_id 为空或过长。")
        if len(binding.source_path) > cls.MAX_PATH_CHARS or not cls.SOURCE_PATH_PATTERN.fullmatch(
            binding.source_path
        ):
            raise ToolResultBindingError("绑定只能读取上游结构化 metadata.raw。")

    def resolve(
        self,
        *,
        call: PlannedToolCall,
        sources_by_call_id: dict[str, list[ExternalSource]],
        definition: ToolDefinition,
    ) -> tuple[PlannedToolCall, list[ToolTraceEvent]]:
        if not call.result_bindings:
            return call, []
        if len(call.result_bindings) > self.MAX_BINDINGS:
            raise ToolResultBindingError("单个工具调用的结果绑定超过上限。")

        arguments = dict(call.arguments)
        events: list[ToolTraceEvent] = []
        for binding in call.result_bindings:
            self.validate_declaration(binding)
            if binding.source_call_id not in call.depends_on:
                raise ToolResultBindingError("绑定来源必须同时声明为 depends_on。")
            sources = sources_by_call_id.get(binding.source_call_id)
            if sources is None:
                raise ToolResultBindingError("绑定来源尚未产生结果。")
            try:
                value = self._resolve_pointer(self._binding_envelope(sources), binding.source_path)
                value = self._validate_bound_value(value)
            except ToolResultBindingError:
                if binding.required:
                    raise
                continue
            arguments[binding.target_argument] = value
            events.append(
                ToolTraceEvent(
                    type="tool_result_binding",
                    payload={
                        "call_id": call.call_id,
                        "tool_key": call.tool_key,
                        "source_call_id": binding.source_call_id,
                        "source_path": binding.source_path,
                        "target_argument": binding.target_argument,
                        "value_type": type(value).__name__,
                        "status": "resolved",
                    },
                )
            )

        defaults = dict(definition.adapter.get("default_arguments") or {})
        fixed_arguments = dict(definition.adapter.get("fixed_arguments") or {})
        try:
            validated = self.validator.validate(
                definition=definition,
                arguments={**defaults, **arguments, **fixed_arguments},
            )
        except ToolSchemaValidationError as exc:
            raise ToolResultBindingError("绑定后的工具参数不符合 Input Schema。") from exc
        model_arguments = {key: value for key, value in validated.items() if key not in fixed_arguments}
        return replace(call, arguments=model_arguments), events

    @staticmethod
    def _binding_envelope(sources: list[ExternalSource]) -> dict[str, Any]:
        return {
            "sources": [
                {"metadata": {"raw": (source.metadata or {}).get("raw")}}
                for source in sources
            ]
        }

    @classmethod
    def _validate_bound_value(cls, value: Any) -> Any:
        if value is None or isinstance(value, dict):
            raise ToolResultBindingError("绑定结果为空或不是受支持的标量。")
        if isinstance(value, str):
            bounded = value.strip()
            if not bounded or len(bounded) > cls.MAX_STRING_CHARS:
                raise ToolResultBindingError("绑定字符串为空或超过长度上限。")
            return bounded
        if isinstance(value, float) and not math.isfinite(value):
            raise ToolResultBindingError("绑定数值不是有限数。")
        if isinstance(value, (bool, int, float)):
            return value
        if isinstance(value, list):
            if len(value) > cls.MAX_ARRAY_ITEMS:
                raise ToolResultBindingError("绑定数组超过元素数量上限。")
            if any(isinstance(item, (dict, list)) or item is None for item in value):
                raise ToolResultBindingError("绑定数组只能包含非空标量。")
            return [cls._validate_bound_value(item) for item in value]
        raise ToolResultBindingError("绑定结果类型不受支持。")

    @staticmethod
    def _resolve_pointer(document: Any, pointer: str) -> Any:
        current = document
        for raw_part in pointer.split("/")[1:]:
            part = raw_part.replace("~1", "/").replace("~0", "~")
            if isinstance(current, dict):
                if part not in current:
                    raise ToolResultBindingError("绑定路径不存在。")
                current = current[part]
            elif isinstance(current, list):
                if not part.isdigit() or int(part) >= len(current):
                    raise ToolResultBindingError("绑定数组下标不存在。")
                current = current[int(part)]
            else:
                raise ToolResultBindingError("绑定路径穿过了非结构化值。")
        return current
