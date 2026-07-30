from __future__ import annotations

import time

from sqlalchemy.orm import Session

from app.services.tools.adapters import ToolAdapterRunner
from app.services.tools.catalog import ToolCatalog
from app.services.tools.credentials import ToolCredentialResolver
from app.services.tools.schemas import PlannedToolCall, ToolCallResult, ToolTraceEvent, redact_sensitive_arguments
from app.services.tools.providers.workspace_files import WorkspaceFileToolProvider


class ToolExecutor:
    def __init__(
        self,
        *,
        credential_resolver: ToolCredentialResolver | None = None,
        catalog: ToolCatalog | None = None,
        adapter_runner: ToolAdapterRunner | None = None,
        db: Session | None = None,
        user_id: str | None = None,
        project_id: str | None = None,
    ) -> None:
        self.catalog = catalog or ToolCatalog()
        self.adapter_runner = adapter_runner or ToolAdapterRunner(
            workspace_file_provider=WorkspaceFileToolProvider(
                db=db,
                user_id=user_id,
                project_id=project_id,
            )
        )
        self.credential_resolver = credential_resolver or ToolCredentialResolver()
        self.user_id = user_id
        self.project_id = project_id

    async def execute(self, call: PlannedToolCall) -> tuple[ToolCallResult, list[ToolTraceEvent]]:
        definition = self.catalog.get_or_none(call.tool_key)
        if not definition:
            return self._skipped(call, f"未知工具：{call.tool_key}")

        events: list[ToolTraceEvent] = []
        events.append(
            ToolTraceEvent(
                type="tool_policy_check",
                payload={
                    "call_id": call.call_id,
                    "tool_key": call.tool_key,
                    "provider": call.provider,
                    "category": call.category,
                    "display_name": call.display_name,
                    "risk_level": definition.risk_level,
                    "read_only": definition.read_only,
                    "adapter_type": definition.adapter_type,
                    "source_type": definition.source_type,
                    "status": "checking",
                },
            )
        )
        if not self.credential_resolver.is_tool_enabled_for_workspace(
            project_id=self.project_id,
            tool_key=call.tool_key,
        ):
            events.append(
                ToolTraceEvent(
                    type="tool_policy_check",
                    payload={
                        "call_id": call.call_id,
                        "tool_key": call.tool_key,
                        "provider": call.provider,
                        "category": call.category,
                        "display_name": call.display_name,
                        "risk_level": definition.risk_level,
                        "read_only": definition.read_only,
                        "status": "denied",
                        "reason": "当前工作区已禁用该工具。",
                    },
                )
            )
            result, skipped_events = self._skipped(call, "当前工作区已禁用该工具。")
            return result, [*events, *skipped_events]

        requires_confirmation = bool(not definition.read_only or definition.risk_level == "high")
        if requires_confirmation:
            # 当前只实现“检测后阻断 + 产生确认事件”，尚无确认后恢复同一调用的 continuation。
            events.append(
                ToolTraceEvent(
                    type="tool_confirmation_required",
                    payload={
                        "call_id": call.call_id,
                        "tool_key": call.tool_key,
                        "provider": call.provider,
                        "category": call.category,
                        "display_name": call.display_name,
                        "risk_level": definition.risk_level,
                        "read_only": definition.read_only,
                        "status": "blocked",
                        "reason": "高风险或非只读工具需要用户确认，当前版本默认不直接执行。",
                    },
                )
            )
            result, skipped_events = self._skipped(call, "高风险或非只读工具需要用户确认后才能执行。")
            return result, [*events, *skipped_events]

        credential = (
            self.credential_resolver.resolve(
                user_id=self.user_id,
                provider_key=definition.credential_provider,
            )
            if definition.credential_required
            else None
        )
        credential_enabled = bool(credential.is_enabled) if credential else True
        credential_source = credential.source if credential else "not_required"
        events.append(
            ToolTraceEvent(
                type="tool_policy_check",
                payload={
                    "call_id": call.call_id,
                    "tool_key": call.tool_key,
                    "provider": call.provider,
                    "category": call.category,
                    "display_name": call.display_name,
                    "risk_level": definition.risk_level,
                    "read_only": definition.read_only,
                    "status": "passed" if credential_enabled else "denied",
                    "credential_provider": definition.credential_provider,
                    "credential_source": credential_source,
                    "credential_required": definition.credential_required,
                    "requires_confirmation": False,
                    "reason": "只读低风险工具，允许执行。"
                    if credential_enabled
                    else None,
                },
            )
        )
        if not credential_enabled:
            result, skipped_events = self._skipped(call, f"工具 provider {definition.credential_provider} 未启用或未配置凭证。")
            return result, [*events, *skipped_events]

        events.append(
            ToolTraceEvent(
                type="tool_call_start",
                payload={
                    "call_id": call.call_id,
                    "tool_key": call.tool_key,
                    "provider": call.provider,
                    "category": call.category,
                    "display_name": call.display_name,
                    "arguments": redact_sensitive_arguments(call.arguments),
                    "credential_source": credential_source,
                    "adapter_type": definition.adapter_type,
                    "source_type": definition.source_type,
                },
            )
        )
        started = time.perf_counter()
        try:
            sources, adapter_metadata = await self.adapter_runner.run(
                definition=definition,
                call=call,
                api_key=credential.api_key if credential else None,
            )
            for source_index, source in enumerate(sources, start=1):
                source.metadata.setdefault("call_id", call.call_id)
                source.metadata.setdefault("tool_key", call.tool_key)
                source.metadata.setdefault("tool_display_name", call.display_name)
                source.metadata.setdefault("source_index", source_index)

            elapsed_ms = int((time.perf_counter() - started) * 1000)
            result = ToolCallResult(
                call=call,
                status="success",
                sources=sources,
                elapsed_ms=elapsed_ms,
            )
            events.append(
                ToolTraceEvent(
                    type="tool_call_end",
                    payload={
                        "call_id": call.call_id,
                        "tool_key": call.tool_key,
                        "provider": call.provider,
                        "category": call.category,
                        "display_name": call.display_name,
                        "status": "success",
                        "elapsed_ms": elapsed_ms,
                        "sources_count": len(sources),
                        "adapter": adapter_metadata,
                    },
                )
            )
            return result, events
        except Exception:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            # Adapter/MCP 的底层异常可能带 endpoint、query 参数或远端响应正文，不能进入 Trace。
            safe_error = f"{call.display_name}调用失败，请稍后重试。"
            result = ToolCallResult(
                call=call,
                status="error",
                sources=[],
                elapsed_ms=elapsed_ms,
                error_message=safe_error,
            )
            events.append(
                ToolTraceEvent(
                    type="tool_call_error",
                    payload={
                        "call_id": call.call_id,
                        "tool_key": call.tool_key,
                        "provider": call.provider,
                        "category": call.category,
                        "display_name": call.display_name,
                        "status": "error",
                        "elapsed_ms": elapsed_ms,
                        "error": safe_error,
                    },
                )
            )
            return result, events

    @staticmethod
    def _skipped(call: PlannedToolCall, reason: str) -> tuple[ToolCallResult, list[ToolTraceEvent]]:
        result = ToolCallResult(
            call=call,
            status="skipped",
            sources=[],
            elapsed_ms=0,
            error_message=reason,
        )
        events = [
            ToolTraceEvent(
                type="tool_call_error",
                payload={
                    "call_id": call.call_id,
                    "tool_key": call.tool_key,
                    "provider": call.provider,
                    "category": call.category,
                    "display_name": call.display_name,
                    "status": "skipped",
                    "elapsed_ms": 0,
                    "error": reason,
                },
            )
        ]
        return result, events
