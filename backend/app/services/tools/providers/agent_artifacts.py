from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.agent_runtime import AgentArtifact, AgentRun
from app.services.tools.schemas import ExternalSource, PlannedToolCall, ToolExecutionFeedbackError


class AgentArtifactToolProvider:
    """Bounded, tenant-scoped just-in-time access to durable Tool results."""

    MAX_LIST_RESULTS = 20
    MAX_READ_CHARS = 12_000

    def __init__(self, *, db: Session | None, user_id: str | None, project_id: str | None) -> None:
        self.db = db
        self.user_id = user_id
        self.project_id = project_id

    async def run(self, *, call: PlannedToolCall) -> tuple[list[ExternalSource], dict[str, Any]]:
        if not self.db or not self.user_id:
            raise ToolExecutionFeedbackError("Agent Artifact 工具缺少用户数据库上下文。")
        if not self.project_id:
            raise ToolExecutionFeedbackError("Agent Artifact 工具必须在明确的工作区中使用。")
        if call.tool_key == "agent.artifacts.list":
            return self._list(call)
        if call.tool_key == "agent.artifacts.read":
            return self._read(call)
        raise ToolExecutionFeedbackError("未知 Agent Artifact 工具。")

    def _base_statement(self):
        statement = (
            select(AgentArtifact, AgentRun)
            .join(AgentRun, AgentRun.id == AgentArtifact.run_id)
            .where(AgentArtifact.user_id == self.user_id, AgentRun.user_id == self.user_id)
        )
        return statement.where(AgentRun.project_id == self.project_id)

    def _list(self, call: PlannedToolCall) -> tuple[list[ExternalSource], dict[str, Any]]:
        run_id = str(call.arguments.get("run_id") or "").strip()
        limit = self._bounded_int(call.arguments.get("limit"), default=10, lower=1, upper=self.MAX_LIST_RESULTS)
        statement = self._base_statement()
        if run_id:
            statement = statement.where(AgentArtifact.run_id == run_id)
        rows = self.db.execute(statement.order_by(AgentArtifact.created_at.desc()).limit(limit)).all()
        if not rows:
            return [], {"adapter_type": "agent_artifact", "operation": "list", "artifacts_count": 0}
        lines = [
            f"- artifact_id={artifact.id}; run_id={artifact.run_id}; type={artifact.artifact_type}; "
            f"chars={artifact.char_count}; preview={artifact.preview[:240]}"
            for artifact, _ in rows
        ]
        return (
            [
                ExternalSource(
                    source_type="agent_artifact_list",
                    provider="agent_runtime",
                    title="Agent Artifact 列表",
                    display_text="\n".join(lines),
                    metadata={
                        "raw": {
                            "artifacts": [
                                {
                                    "artifact_id": artifact.id,
                                    "run_id": artifact.run_id,
                                    "step_id": artifact.step_id,
                                    "content_hash": artifact.content_hash,
                                    "char_count": artifact.char_count,
                                }
                                for artifact, _ in rows
                            ]
                        }
                    },
                )
            ],
            {"adapter_type": "agent_artifact", "operation": "list", "artifacts_count": len(rows)},
        )

    def _read(self, call: PlannedToolCall) -> tuple[list[ExternalSource], dict[str, Any]]:
        artifact_id = str(call.arguments.get("artifact_id") or "").strip()
        if not artifact_id:
            raise ToolExecutionFeedbackError("读取 Artifact 缺少 artifact_id。")
        max_chars = self._bounded_int(
            call.arguments.get("max_chars"), default=6000, lower=200, upper=self.MAX_READ_CHARS
        )
        row = self.db.execute(self._base_statement().where(AgentArtifact.id == artifact_id).limit(1)).first()
        if not row:
            # Do not reveal whether another user/project owns this ID.
            raise ToolExecutionFeedbackError("当前工作区中未找到该 Artifact。")
        artifact, _ = row
        content = artifact.content_json[:max_chars]
        truncated = len(artifact.content_json) > len(content)
        if truncated:
            content += "\n[Artifact 内容已达本次读取上限]"
        return (
            [
                ExternalSource(
                    source_type="agent_artifact_read",
                    provider="agent_runtime",
                    title=f"Agent Artifact {artifact.id}",
                    display_text=content,
                    metadata={
                        "artifact_id": artifact.id,
                        "run_id": artifact.run_id,
                        "step_id": artifact.step_id,
                        "content_hash": artifact.content_hash,
                        "truncated": truncated,
                        "raw": {
                            "artifact_id": artifact.id,
                            "run_id": artifact.run_id,
                            "step_id": artifact.step_id,
                            "content_hash": artifact.content_hash,
                        },
                    },
                )
            ],
            {
                "adapter_type": "agent_artifact",
                "operation": "read",
                "artifact_id": artifact.id,
                "returned_chars": len(content),
                "truncated": truncated,
            },
        )

    @staticmethod
    def _bounded_int(value: Any, *, default: int, lower: int, upper: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return default
        return max(lower, min(parsed, upper))
