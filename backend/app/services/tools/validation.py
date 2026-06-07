from __future__ import annotations

from typing import Any

from app.services.tools.schemas import ToolDefinition


class ToolSchemaValidationError(ValueError):
    pass


class ToolSchemaValidator:
    """Small JSON-schema subset validator for tool arguments.

    The manifest currently uses a constrained subset: object properties,
    required fields, primitive types, arrays, enum, minimum and maximum.
    Keeping this local avoids adding another dependency for the first version.
    """

    def validate(self, *, definition: ToolDefinition, arguments: dict[str, Any]) -> dict[str, Any]:
        schema = definition.input_schema or {}
        if schema.get("type") and schema.get("type") != "object":
            raise ToolSchemaValidationError(f"{definition.tool_key} input_schema 必须是 object。")

        properties = schema.get("properties") or {}
        required = schema.get("required") or []
        normalized = dict(arguments or {})

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
