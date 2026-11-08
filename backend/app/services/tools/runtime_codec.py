"""工具业务快照的显式类型白名单；不反序列化任意类或可执行对象。"""

from dataclasses import fields, is_dataclass

from app.services.tools.schemas import (
    ExternalSource, PlannedToolCall, ToolCallResult, ToolPlan, ToolResultBinding, ToolTraceEvent,
    redact_sensitive_arguments,
)
from app.services.tools.workflow import (
    ToolStepOutcome, ToolStepResult, ToolWorkflowAggregate, ToolWorkflowFeedback, ToolWorkflowResult,
)

TYPES = {item.__name__: item for item in (
    ExternalSource, PlannedToolCall, ToolCallResult, ToolPlan, ToolResultBinding, ToolTraceEvent,
    ToolStepOutcome, ToolStepResult, ToolWorkflowAggregate, ToolWorkflowFeedback, ToolWorkflowResult,
)}


def encode(value):
    if is_dataclass(value) and type(value).__name__ in TYPES:
        return {"$type": type(value).__name__, "fields": {
            field.name: encode(getattr(value, field.name)) for field in fields(value)}}
    if isinstance(value, tuple):
        return {"$tuple": [encode(item) for item in value]}
    if isinstance(value, list):
        return [encode(item) for item in value]
    if isinstance(value, dict):
        # 字典保留容器标记，外部JSON中的$type不能被解释成内部类。
        return {"$dict": {str(key): encode(item) for key, item in value.items()}}
    if value is None or type(value) in (str, int, float, bool):
        return value
    raise ValueError("工具快照包含不允许持久化的类型")


def decode(value):
    if isinstance(value, list):
        return [decode(item) for item in value]
    if not isinstance(value, dict):
        return value
    if set(value) == {"$dict"}:
        return {key: decode(item) for key, item in value["$dict"].items()}
    if set(value) == {"$tuple"}:
        return tuple(decode(item) for item in value["$tuple"])
    if set(value) == {"$type", "fields"} and value["$type"] in TYPES:
        return TYPES[value["$type"]](**{key: decode(item) for key, item in value["fields"].items()})
    raise ValueError("工具快照类型或版本无法识别")


def dumps(value):
    import json
    # 先脱敏业务对象，再编码，避免内部字段名掩盖原始metadata的敏感键。
    return json.dumps(encode(sanitize(value)), ensure_ascii=False, allow_nan=False)


def loads(value):
    import json
    return decode(json.loads(value))


def sanitize(value):
    if is_dataclass(value):
        return type(value)(**{field.name: sanitize(getattr(value, field.name)) for field in fields(value)})
    if isinstance(value, tuple):
        return tuple(sanitize(item) for item in value)
    if isinstance(value, list):
        return [sanitize(item) for item in value]
    if isinstance(value, dict):
        # 每个节点只遍历一次；大段工具正文不能因重复递归脱敏放大序列化耗时。
        return {str(key): "***" if redact_sensitive_arguments({str(key): None})[str(key)] == "***"
                else sanitize(item) for key, item in value.items()}
    return redact_sensitive_arguments(value)
