from __future__ import annotations

import time

from sqlalchemy.orm import Session

from app.services.tools.adapters import ToolAdapterRunner
from app.services.tools.catalog import ToolCatalog
from app.services.tools.credentials import ToolCredentialResolver
from app.services.tools.schemas import (
    ExternalSource,
    PlannedToolCall,
    ToolCallResult,
    ToolExecutionFeedbackError,
    ToolTraceEvent,
    redact_sensitive_arguments,
)
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
        conversation_id: str | None = None,
        assistant_message_id: str | None = None,
    ) -> None:
        self.catalog = catalog or ToolCatalog()
        self.adapter_runner = adapter_runner or ToolAdapterRunner(
            workspace_file_provider=WorkspaceFileToolProvider(
                db=db,
                user_id=user_id,
                project_id=project_id,
            )
        )
        self.credential_resolver = credential_resolver or ToolCredentialResolver(db)
        self.user_id = user_id
        self.project_id = project_id
        self.db = db
        self.conversation_id = conversation_id
        self.assistant_message_id = assistant_message_id

    async def execute(self, call: PlannedToolCall) -> tuple[ToolCallResult, list[ToolTraceEvent]]:
        definition = self.catalog.get_or_none(call.tool_key)
        if not definition:
            return self._skipped(call, f"未知工具：{call.tool_key}")

        permission_resolver = getattr(self.credential_resolver, "get_workspace_permission_mode", None)
        permission_mode = (
            permission_resolver(project_id=self.project_id)
            if callable(permission_resolver)
            else "ask"
        )

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
                    "permission_mode": permission_mode,
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
            if permission_mode == "read_only":
                events.append(
                    ToolTraceEvent(
                        type="tool_policy_check",
                        payload={
                            "call_id": call.call_id,
                            "tool_key": call.tool_key,
                            "risk_level": definition.risk_level,
                            "read_only": definition.read_only,
                            "permission_mode": permission_mode,
                            "status": "denied",
                            "reason": "当前工作区为只读模式，禁止产生副作用的工具。",
                        },
                    )
                )
                result, skipped_events = self._skipped(call, "当前工作区为只读模式，已阻止该工具。")
                return result, [*events, *skipped_events]
            if call.tool_key == "workspace.files.apply_edit" and self.db and self.user_id:
                from app.services.agent_runtime_service import AgentRuntimeError, AgentRuntimeService

                try:
                    runtime_service = AgentRuntimeService(self.db)
                    proposal = runtime_service.propose_file_edit(
                        user_id=self.user_id,
                        project_id=self.project_id,
                        call_id=call.call_id,
                        file_id=str(call.arguments.get("file_id") or ""),
                        old_string=str(call.arguments.get("old_string") or ""),
                        new_string=str(call.arguments.get("new_string") or ""),
                        depends_on=call.depends_on,
                        conversation_id=self.conversation_id,
                        assistant_message_id=self.assistant_message_id,
                    )
                except AgentRuntimeError as exc:
                    result, skipped_events = self._skipped(call, str(exc))
                    return result, [*events, *skipped_events]
                if permission_mode == "full_workspace":
                    try:
                        applied = runtime_service.apply_trusted_workspace_file_edit(
                            proposal=proposal,
                            user_id=self.user_id,
                        )
                    except AgentRuntimeError as exc:
                        result, skipped_events = self._skipped(call, str(exc))
                        return result, [*events, *skipped_events]
                    events.append(
                        ToolTraceEvent(
                            type="tool_call_end",
                            payload={
                                "call_id": call.call_id,
                                "tool_key": call.tool_key,
                                "status": "success",
                                "permission_mode": permission_mode,
                                "run_id": applied.run_id,
                                "step_id": applied.step_id,
                                "revision_id": applied.revision_id,
                                "revision_number": applied.revision_number,
                                "reason": (
                                    "工作区完全访问模式已放行受 ACL 与版本 CAS 约束的项目文件修改。"
                                ),
                            },
                        )
                    )
                    return (
                        ToolCallResult(
                            call=call,
                            status="success",
                            sources=[
                                ExternalSource(
                                    source_type="workspace_file_revision",
                                    provider="workspace",
                                    title=f"{proposal.file_name} 已更新",
                                    display_text=(
                                        "修改已按工作区权限策略应用，并生成可审计的文件版本。"
                                    ),
                                    metadata={
                                        "call_id": call.call_id,
                                        "tool_key": call.tool_key,
                                        "file_id": applied.file_id,
                                        "run_id": applied.run_id,
                                        "step_id": applied.step_id,
                                        "revision_id": applied.revision_id,
                                        "revision_number": applied.revision_number,
                                        "applied": True,
                                        "permission_mode": permission_mode,
                                    },
                                )
                            ],
                            elapsed_ms=0,
                        ),
                        events,
                    )
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
                            "read_only": False,
                            "permission_mode": permission_mode,
                            "status": "waiting_approval",
                            "run_id": proposal.run_id,
                            "step_id": proposal.step_id,
                            "patch_draft_id": proposal.patch_draft_id,
                            "approval_id": proposal.approval_id,
                            "file_id": proposal.file_id,
                            "file_name": proposal.file_name,
                            "diff_text": proposal.diff_text,
                            "arguments_hash": proposal.arguments_hash,
                            "expires_at": proposal.expires_at.isoformat(),
                            "reason": "已生成持久化 Diff，需用户确认后才能以版本 CAS 写入。",
                        },
                    )
                )
                return (
                    ToolCallResult(
                        call=call,
                        status="confirmation_required",
                        sources=[
                            ExternalSource(
                                source_type="workspace_file_edit_approval",
                                provider="workspace",
                                title=f"{proposal.file_name} 修改提案（等待确认）",
                                display_text=(
                                    "以下 Diff 已持久化，但尚未写入。用户必须在界面确认；"
                                    "在收到 applied 状态前，不得声称修改完成。\n"
                                    f"{proposal.diff_text}"
                                ),
                                metadata={
                                    "call_id": call.call_id,
                                    "tool_key": call.tool_key,
                                    "file_id": proposal.file_id,
                                    "run_id": proposal.run_id,
                                    "step_id": proposal.step_id,
                                    "approval_id": proposal.approval_id,
                                    "applied": False,
                                },
                            )
                        ],
                        elapsed_ms=0,
                        error_message="文件修改提案正在等待用户确认。",
                    ),
                    events,
                )
            # Full workspace access is not host access and never auto-authorizes
            # arbitrary MCP writes or external side effects.
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
                        "permission_mode": permission_mode,
                        "status": "blocked",
                        "reason": (
                            "该能力不在工作区自动放行清单中；外部副作用仍需专用审批与幂等实现。"
                        ),
                    },
                )
            )
            result, skipped_events = self._skipped(
                call,
                "该高风险能力尚无安全 continuation，当前版本拒绝执行。",
            )
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
                    "permission_mode": permission_mode,
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
        except ToolExecutionFeedbackError as exc:
            elapsed_ms = int((time.perf_counter() - started) * 1000)
            safe_error = str(exc)[:500]
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
                        "error_kind": "tool_feedback",
                        "elapsed_ms": elapsed_ms,
                        "error": safe_error,
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
