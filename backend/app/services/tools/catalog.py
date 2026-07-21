from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.models.tool_config import McpServer, McpTool
from app.repositories.tool_config_repo import ToolConfigRepository
from app.services.tools.schemas import ToolDefinition


class ToolCatalog:
    """Loads tool definitions from manifest files.

    The catalog is intentionally read-only for now. Runtime enable/disable and
    credentials still live in the DB-backed tool settings layer.
    """

    def __init__(
        self,
        manifest_path: Path | None = None,
        *,
        db: Session | None = None,
        user_id: str | None = None,
    ) -> None:
        self.manifest_path = manifest_path or self._default_manifest_path()
        self._definitions = self._load_definitions(self.manifest_path)
        if db and user_id:
            self._definitions.update(self._load_db_mcp_definitions(db=db, user_id=user_id))

    @staticmethod
    def _default_manifest_path() -> Path:
        return Path(__file__).resolve().parents[2] / "tool_manifests" / "default_tools.json"

    def get(self, tool_key: str) -> ToolDefinition:
        return self._definitions[tool_key]

    def get_or_none(self, tool_key: str) -> ToolDefinition | None:
        return self._definitions.get(tool_key)

    def list_definitions(self) -> list[ToolDefinition]:
        return list(self._definitions.values())

    def find_by_category(self, category: str) -> list[ToolDefinition]:
        return [tool for tool in self._definitions.values() if tool.category == category]

    def first_by_category(self, category: str) -> ToolDefinition:
        matches = self.find_by_category(category)
        if not matches:
            raise KeyError(f"No tool registered for category: {category}")
        return matches[0]

    @classmethod
    def _load_definitions(cls, manifest_path: Path) -> dict[str, ToolDefinition]:
        records = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(records, list):
            raise ValueError("Tool manifest must be a JSON array.")

        definitions: dict[str, ToolDefinition] = {}
        for record in records:
            definition = cls._parse_record(record)
            if definition.tool_key in definitions:
                raise ValueError(f"Duplicated tool_key in manifest: {definition.tool_key}")
            definitions[definition.tool_key] = definition
        return definitions

    @staticmethod
    def _parse_record(record: dict[str, Any]) -> ToolDefinition:
        required = ["tool_key", "provider", "category", "display_name", "description"]
        missing = [key for key in required if not record.get(key)]
        if missing:
            raise ValueError(f"Tool manifest record missing required fields: {', '.join(missing)}")

        adapter = record.get("adapter") or {}
        if not isinstance(adapter, dict):
            raise ValueError(f"Tool adapter must be an object: {record['tool_key']}")

        return ToolDefinition(
            tool_key=str(record["tool_key"]),
            provider=str(record["provider"]),
            category=str(record["category"]),
            display_name=str(record["display_name"]),
            description=str(record["description"]),
            when_to_use=list(record.get("when_to_use") or []),
            when_not_to_use=list(record.get("when_not_to_use") or []),
            input_schema=dict(record.get("input_schema") or {}),
            output_schema=dict(record.get("output_schema") or {}),
            adapter_type=str(record.get("adapter_type") or adapter.get("type") or "python"),
            adapter=adapter,
            source_type=str(record.get("source_type") or "local_manifest"),
            risk_level=str(record.get("risk_level") or "low"),
            fallback_tool_key=record.get("fallback_tool_key"),
            enabled_by_default=bool(record.get("enabled_by_default", True)),
            read_only=bool(record.get("read_only", True)),
        )

    @classmethod
    def _load_db_mcp_definitions(cls, *, db: Session, user_id: str) -> dict[str, ToolDefinition]:
        definitions: dict[str, ToolDefinition] = {}
        for tool, server in ToolConfigRepository(db).list_mcp_tools(user_id=user_id, enabled_only=True):
            definition = cls._parse_mcp_tool(tool=tool, server=server)
            definitions[definition.tool_key] = definition
        return definitions

    @staticmethod
    def _json_loads(value: str | None, fallback: Any) -> Any:
        if not value:
            return fallback
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return fallback

    @classmethod
    def _parse_mcp_tool(cls, *, tool: McpTool, server: McpServer) -> ToolDefinition:
        input_schema = cls._json_loads(tool.input_schema_json, {})
        output_schema = cls._json_loads(tool.output_schema_json, {})
        fixed_arguments = cls._json_loads(tool.fixed_arguments_json, {})
        credential_provider = (server.credential_provider or server.server_key).strip()
        description = (tool.description_override or tool.description or f"{server.name} MCP tool: {tool.raw_name}").strip()
        return ToolDefinition(
            tool_key=tool.tool_key,
            provider=server.server_key[:64],
            category=tool.category or "mcp_tool",
            display_name=tool.display_name[:128],
            description=description,
            when_to_use=[description],
            when_not_to_use=[],
            input_schema=input_schema if isinstance(input_schema, dict) else {},
            output_schema=output_schema if isinstance(output_schema, dict) else {},
            adapter_type="mcp_http",
            adapter={
                "endpoint_template": server.url,
                "mcp_tool_name": tool.raw_name,
                "result_mapper": "",
                "credential_provider": credential_provider,
                "auth_type": server.auth_type,
                # User-configured fixed arguments are policy-owned values, not
                # model-overridable defaults (tenant/account/scope are common examples).
                "fixed_arguments": fixed_arguments if isinstance(fixed_arguments, dict) else {},
            },
            source_type="mcp_server",
            risk_level=tool.risk_level or "low",
            fallback_tool_key=None,
            enabled_by_default=server.is_enabled and tool.is_enabled,
            read_only=tool.read_only,
        )
