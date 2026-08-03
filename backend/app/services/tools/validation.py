from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from app.services.tools.schemas import ToolDefinition


class ToolSchemaValidationError(ValueError):
    pass


class ToolSchemaValidator:
    """Normalize common model output forms, then enforce full Draft 2020-12 JSON Schema."""

    def validate(
        self,
        *,
        definition: ToolDefinition,
        arguments: dict[str, Any],
        deferred_required_fields: set[str] | None = None,
    ) -> dict[str, Any]:
        schema = definition.input_schema or {}
        if schema.get("type") and schema.get("type") != "object":
            raise ToolSchemaValidationError(f"{definition.tool_key} input_schema 必须是 object。")

        if not isinstance(arguments, dict):
            raise ToolSchemaValidationError(f"{definition.tool_key} 工具参数必须是 object。")

        properties = schema.get("properties") or {}
        deferred = deferred_required_fields or set()
        required = [field for field in (schema.get("required") or []) if field not in deferred]
        normalized = dict(arguments or {})

        if schema.get("additionalProperties") is False:
            unexpected = sorted(set(normalized) - set(properties))
            if unexpected:
                raise ToolSchemaValidationError(
                    f"{definition.tool_key} 包含 schema 未声明的参数：{', '.join(unexpected)}"
                )

        for field in required:
            if normalized.get(field) in (None, "", []):
                raise ToolSchemaValidationError(f"{definition.tool_key} 缺少必填参数：{field}")

        for field, value in list(normalized.items()):
            field_schema = properties.get(field)
            if not field_schema:
                continue
            normalized[field] = self._validate_value(
                tool_key=definition.tool_key,
                field=field,
                value=value,
                schema=field_schema,
            )
        try:
            validation_schema = {**schema, "required": required} if deferred else schema
            Draft202012Validator.check_schema(validation_schema)
            errors = sorted(
                Draft202012Validator(validation_schema).iter_errors(normalized),
                key=lambda item: list(item.path),
            )
        except SchemaError as exc:
            raise ToolSchemaValidationError(f"{definition.tool_key} input_schema 不合法。") from exc
        if errors:
            first = errors[0]
            path = ".".join(str(part) for part in first.path)
            location = f".{path}" if path else ""
            raise ToolSchemaValidationError(
                f"{definition.tool_key}{location} 不符合 JSON Schema：{first.validator}"
            )
        return normalized

    def _validate_value(self, *, tool_key: str, field: str, value: Any, schema: dict[str, Any]) -> Any:
        expected_type = schema.get("type")
        if expected_type == "string":
            if isinstance(value, list):
                value = "|".join(str(item).strip() for item in value if str(item).strip())
            if not isinstance(value, str):
                value = str(value)
            value = value.strip()
        elif expected_type == "integer":
            try:
                value = int(value)
            except (TypeError, ValueError) as exc:
                raise ToolSchemaValidationError(f"{tool_key}.{field} 必须是整数。") from exc
            if "minimum" in schema and value < int(schema["minimum"]):
                value = int(schema["minimum"])
            if "maximum" in schema and value > int(schema["maximum"]):
                value = int(schema["maximum"])
        elif expected_type == "array":
            if isinstance(value, str):
                value = [item.strip() for item in value.split("|") if item.strip()]
            if not isinstance(value, list):
                raise ToolSchemaValidationError(f"{tool_key}.{field} 必须是数组。")
            item_schema = schema.get("items") or {}
            if item_schema.get("type") == "string":
                value = [str(item).strip() for item in value if str(item).strip()]
        elif expected_type and not self._matches_json_type(value, expected_type):
            raise ToolSchemaValidationError(f"{tool_key}.{field} 类型不符合 schema：{expected_type}")

        enum = schema.get("enum")
        if enum and value not in enum:
            default = schema.get("default")
            if default in enum:
                return default
            raise ToolSchemaValidationError(f"{tool_key}.{field} 必须是：{', '.join(map(str, enum))}")
        return value

    @staticmethod
    def _matches_json_type(value: Any, expected_type: str) -> bool:
        if expected_type == "number":
            return isinstance(value, (int, float))
        if expected_type == "boolean":
            return isinstance(value, bool)
        if expected_type == "object":
            return isinstance(value, dict)
        return True
