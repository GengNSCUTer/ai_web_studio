from __future__ import annotations

import json
import re
from typing import Any

from sqlalchemy.orm import Session

from app.models.agent_runtime import AgentArtifact
from app.services.tools.schemas import ExternalSource, PlannedToolCall, ToolExecutionFeedbackError
from app.services.agent_artifact_store import AgentArtifactStore, POLICIES


class AgentArtifactToolProvider:
    """Bounded, tenant-scoped just-in-time access to durable Tool results."""

    MAX_LIST_RESULTS = 20
    MAX_READ_CHARS = 12_000
    ACCESS_SCOPE = "current_user_current_project"

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
            return await self._list(call)
        if call.tool_key == "agent.artifacts.read":
            return self._read(call)
        raise ToolExecutionFeedbackError("未知 Agent Artifact 工具。")

    def _base_statement(self):
        return AgentArtifactStore(self.db).visible_statement(user_id=self.user_id, project_id=self.project_id)

    async def _list(self, call: PlannedToolCall) -> tuple[list[ExternalSource], dict[str, Any]]:
        run_id = str(call.arguments.get("run_id") or "").strip()
        limit = self._bounded_int(call.arguments.get("limit"), default=10, lower=1, upper=self.MAX_LIST_RESULTS)
        max_age = self._bounded_int(call.arguments.get("max_age_hours"), default=168, lower=1, upper=720)
        query = str(call.arguments.get("query") or "").strip()[:300]
        logical_type = str(call.arguments.get("artifact_type") or "").strip()
        statement = self._base_statement()
        if run_id:
            statement = statement.where(AgentArtifact.run_id == run_id)
        if logical_type:
            statement = statement.where(AgentArtifact.artifact_type.in_(
                [key for key, policy in POLICIES.items() if policy.logical_type == logical_type]))
        candidates = self.db.execute(statement.order_by(AgentArtifact.created_at.desc(), AgentArtifact.id.desc()).limit(60)).all()
        store = AgentArtifactStore(self.db)
        ranked = [(self._query_score(query, artifact.preview), -index, artifact, run)
            for index, (artifact, run) in enumerate(candidates) if store.reusable(run, artifact, max_age_hours=max_age)]
        ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
        retrieval = "bounded_local_query_and_recency"
        if query and ranked and call.arguments.get("semantic", False):
            from app.services.artifact_semantic_ranker import ArtifactSemanticRanker
            from app.services.tools.db_boundary import release_read_transaction
            previews = [(item[2].id, item[2].preview) for item in ranked[:24]]
            release_read_transaction(self.db)
            scores, retrieval = await ArtifactSemanticRanker().rank(user_id=self.user_id, query=query, previews=previews)
            store = AgentArtifactStore(self.db)
            semantic_order = {key: index for index, key in enumerate(sorted(scores, key=lambda key: -scores[key]), 1)
                if scores[key] >= .35}
            ranked = sorted([(1/(60+index) + (1/(60+semantic_order[item[2].id]) if item[2].id in semantic_order else 0),
                item[1], item[2], item[3]) for index, item in enumerate(ranked, 1)
                if item[0] > 0 or item[2].id in semantic_order], key=lambda item: (item[0], item[1]), reverse=True)
        elif query:
            ranked = [item for item in ranked if item[0] > 0]
        rows = [(item[2], item[3]) for item in ranked[:limit] if store.reusable(item[3], item[2], max_age_hours=max_age)]
        if not rows:
            return (
                [
                    ExternalSource(
                        source_type="agent_artifact_list",
                        provider="agent_runtime",
                        title="Agent Artifact 列表",
                        display_text="当前项目没有可读取的历史 Agent 产物。",
                        metadata={
                            "empty_reason": "no_artifacts",
                            "result_semantics": "empty_answer",
                            "raw": {"artifacts": []},
                        },
                    )
                ],
                {
                    "adapter_type": "agent_artifact",
                    "operation": "list",
                    "artifacts_count": 0,
                    "result_semantics": "empty_answer",
                    "scanned_candidates": len(candidates), "retrieval": retrieval,
                },
            )
        lines = [
            f"- artifact_id={artifact.id}; run_id={artifact.run_id}; type={artifact.artifact_type}; "
            f"kind={POLICIES[artifact.artifact_type].logical_type}; snapshot_at={artifact.created_at.isoformat()}; "
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
                                    "access_scope": self.ACCESS_SCOPE,
                                    "artifact_type": POLICIES[artifact.artifact_type].logical_type,
                                    "format_version": 1,
                                    "snapshot_at": artifact.created_at.isoformat(),
                                    "retention": POLICIES[artifact.artifact_type].retention,
                                }
                                for artifact, _ in rows
                            ]
                        }
                    },
                )
            ],
            {"adapter_type": "agent_artifact", "operation": "list", "artifacts_count": len(rows),
                "scanned_candidates": len(candidates), "retrieval": retrieval},
        )

    @staticmethod
    def _query_score(query, preview):
        if not query:
            return 1
        query, preview = query.casefold(), preview.casefold()[:3000]
        terms = re.findall(r"[a-z0-9_]{2,}|[\u4e00-\u9fff]{2,}", query)
        terms = {piece for term in terms for piece in
            ([term] if term.isascii() else [term[i:i+2] for i in range(len(term)-1)])}
        return sum(1 for term in terms if term in preview)

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
        artifact, run = row
        if not AgentArtifactStore(self.db).reusable(run, artifact,
                max_age_hours=self._bounded_int(call.arguments.get("max_age_hours"), default=168, lower=1, upper=720)):
            raise ToolExecutionFeedbackError("该产物已过期、来源变化或当前范围不再允许使用；请重新取得证据。")
        visible = artifact.content_json
        if artifact.artifact_type == "document_export":
            # 二进制文档供下载；工具按需读取正文与来源，避免把 base64 当证据送给模型。
            payload = json.loads(visible)
            visible = json.dumps({k: v for k, v in payload.items() if k != "data_base64"}, ensure_ascii=False)
        elif artifact.artifact_type == "tool_result":
            visible = json.dumps({k: v for k, v in json.loads(visible).items() if k != "events"}, ensure_ascii=False)
        content = visible[:max_chars]
        truncated = len(visible) > len(content)
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
                        "access_scope": self.ACCESS_SCOPE,
                        "truncated": truncated,
                        "artifact_type": POLICIES[artifact.artifact_type].logical_type,
                        "format_version": 1,
                        "snapshot_at": artifact.created_at.isoformat(),
                        "historical_snapshot": True,
                        "raw": {
                            "artifact_id": artifact.id,
                            "run_id": artifact.run_id,
                            "step_id": artifact.step_id,
                            "content_hash": artifact.content_hash,
                            "access_scope": self.ACCESS_SCOPE,
                            "content": content,
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
