"""共用产物存储与可见性；保留物理类型，统一逻辑类型和授权生命周期。"""

from dataclasses import dataclass
from datetime import timedelta
import hashlib
import json

from sqlalchemy import select

from app.models.agent_runtime import AgentArtifact, AgentRun, AgentStep
from app.models.project import Project
from app.models.project_file import ProjectFile
from app.models.message import Message
from app.models.conversation import Conversation
from app.services.runtime_lease import aware, utcnow


class ArtifactStoreError(ValueError):
    pass


@dataclass(frozen=True)
class ArtifactPolicy:
    logical_type: str
    visible_to_tools: bool
    retention: str = "until_run_deleted"


POLICIES = {
    "tool_result": ArtifactPolicy("tool_result", True),
    "chat_tool_result": ArtifactPolicy("tool_result", False),
    "document_evidence": ArtifactPolicy("evidence_snapshot", True),
    "document_draft": ArtifactPolicy("document_draft", True),
    "document_export": ArtifactPolicy("exported_file", True),
    "document_delivery": ArtifactPolicy("delivery_receipt", True),
    "chat_generation_partial": ArtifactPolicy("generation_partial", False),
}
LOGICAL_VISIBLE_TYPES = tuple(sorted({p.logical_type for p in POLICIES.values() if p.visible_to_tools}))
VISIBLE_STORAGE_TYPES = tuple(key for key, policy in POLICIES.items() if policy.visible_to_tools)


class AgentArtifactStore:
    def __init__(self, db):
        self.db = db
        self._catalogs = {}

    @staticmethod
    def validate_scope(run, step, artifact=None):
        if not run.id or not run.user_id or not step.id or step.run_id != run.id:
            raise ArtifactStoreError("产物步骤与当前运行不一致。")
        if artifact and (artifact.run_id != run.id or artifact.step_id != step.id or artifact.user_id != run.user_id):
            raise ArtifactStoreError("产物不属于当前用户、运行和步骤。")

    @staticmethod
    def verify_content(artifact):
        if hashlib.sha256(artifact.content_json.encode()).hexdigest() != artifact.content_hash:
            raise ArtifactStoreError("产物内容校验失败。")

    def create(self, *, run, step, content_json, artifact_type, preview="", prompt_state="stored",
               max_chars=None, max_bytes=None):
        self.validate_scope(run, step)
        if artifact_type not in POLICIES or not isinstance(content_json, str):
            raise ArtifactStoreError("产物类型或内容格式不受支持。")
        try:
            def reject_constant(value):
                raise ValueError(value)
            json.loads(content_json, parse_constant=reject_constant)
        except ValueError as exc:
            raise ArtifactStoreError("产物不是合法有限JSON。") from exc
        if ((max_chars is not None and len(content_json) > max_chars)
                or (max_bytes is not None and len(content_json.encode()) > max_bytes)):
            raise ArtifactStoreError("产物超过持久化大小限制。")
        artifact = AgentArtifact(run_id=run.id, step_id=step.id, user_id=run.user_id,
            artifact_type=artifact_type, content_hash=hashlib.sha256(content_json.encode()).hexdigest(),
            preview=preview, content_json=content_json, char_count=len(content_json), prompt_state=prompt_state)
        self.db.add(artifact)
        self.db.flush()
        return artifact

    def visible_statement(self, *, user_id, project_id):
        return (select(AgentArtifact, AgentRun).join(AgentRun, AgentRun.id == AgentArtifact.run_id)
            .join(Project, Project.id == AgentRun.project_id)
            .join(AgentStep, AgentStep.id == AgentArtifact.step_id)
            .where(AgentArtifact.user_id == user_id, AgentRun.user_id == user_id,
                Project.user_id == user_id, Project.id == project_id, AgentStep.run_id == AgentRun.id,
                AgentStep.status == "succeeded", AgentArtifact.artifact_type.in_(VISIBLE_STORAGE_TYPES),
                AgentArtifact.prompt_state.notin_(("revoked", "expired", "archived"))))

    def reusable(self, run, artifact, *, max_age_hours=168, _visited=None, _budget=None):
        """只判断当前是否可作为历史证据，不授予执行原工具或写入的权限。"""
        current = self.db.get(AgentArtifact, artifact.id, populate_existing=True)
        current_run = self.db.get(AgentRun, run.id, populate_existing=True)
        if not current or not current_run or current.run_id != current_run.id:
            return False
        artifact, run = current, current_run
        budget = _budget if _budget is not None else [64]
        budget[0] -= 1
        if budget[0] < 0:
            return False
        policy = POLICIES.get(artifact.artifact_type)
        if not policy or not policy.visible_to_tools:
            return False
        if artifact.prompt_state in {"revoked", "expired", "archived"}:
            return False
        if not artifact.created_at or aware(artifact.created_at) < utcnow() - timedelta(hours=max_age_hours):
            return False
        visited = set(_visited or ())
        if artifact.id in visited or len(visited) >= 8:
            return False
        visited.add(artifact.id)
        step = self.db.get(AgentStep, artifact.step_id, populate_existing=True)
        project = self.db.get(Project, run.project_id, populate_existing=True) if run.project_id else None
        if not step or step.status != "succeeded" or not project or project.user_id != run.user_id:
            return False
        try:
            self.validate_scope(run, step, artifact)
            self.verify_content(artifact)
            payload = json.loads(artifact.content_json)
            request = json.loads(run.input_json or "{}")
            if artifact.artifact_type == "tool_result":
                from app.services.tools.catalog import ToolCatalog
                from app.services.tools.credentials import ToolCredentialResolver
                from app.services.tools.schemas import tool_execution_snapshot
                scope = (run.user_id, run.project_id)
                if scope not in self._catalogs:
                    self._catalogs[scope] = ToolCatalog(db=self.db, user_id=run.user_id, project_id=run.project_id)
                definition = self._catalogs[scope].get_or_none(step.tool_key)
                if (not definition or not definition.enabled_by_default
                        or not ToolCredentialResolver(self.db).is_tool_enabled_for_workspace(
                            project_id=run.project_id, tool_key=step.tool_key)):
                    return False
                snapshots = json.loads(run.planner_state_json or "{}").get("tool_snapshots", {})
                expected = snapshots.get(step.tool_key)
                if expected and expected != tool_execution_snapshot(definition):
                    return False
                # 高频变化数据不能因用户放宽历史范围而被当作新鲜结果。
                if definition.category in {"weather", "map_route", "map_distance"} and aware(artifact.created_at) < utcnow() - timedelta(minutes=15):
                    return False
            # 文档的每个阶段继承原授权资料，不只检查导出文件本身。
            if run.runtime_kind == "document_workflow":
                for file_id in request.get("file_ids", []):
                    if not self._file_allowed(run, file_id):
                        return False
                message_id = request.get("assistant_message_id")
                if message_id:
                    message = self.db.get(Message, message_id, populate_existing=True)
                    conversation = self.db.get(Conversation, message.conversation_id) if message else None
                    if (not message or not conversation or conversation.user_id != run.user_id
                            or conversation.project_id != run.project_id or message.status != "done"
                            or message.generation_id != request.get("source_generation_id")):
                        return False
                evidence = artifact if artifact.artifact_type == "document_evidence" else self.db.scalar(
                    select(AgentArtifact).where(AgentArtifact.run_id == run.id,
                        AgentArtifact.user_id == run.user_id, AgentArtifact.artifact_type == "document_evidence").limit(1))
                if not evidence:
                    return False
                self.verify_content(evidence)
                for record in json.loads(evidence.content_json).get("sources", []):
                    if record.get("kind") == "project_file":
                        if not self._file_allowed(run, record.get("id"), hashlib.sha256(record["content"].encode()).hexdigest()):
                            return False
                    if record.get("kind") == "assistant_answer":
                        item = self.db.get(Message, record.get("id"), populate_existing=True)
                        if not item or item.content != record.get("content"):
                            return False
            sources = payload.get("sources", [])
            if not isinstance(sources, list) or len(sources) > 128:
                return False
            for source in sources:
                if not isinstance(source, dict):
                    continue
                metadata = source.get("metadata") or {}
                if source.get("kind") == "project_file" and not self._file_allowed(run, source.get("id")):
                    return False
                if isinstance(metadata, dict):
                    if metadata.get("file_id") and not self._file_allowed(run, metadata["file_id"], metadata.get("content_hash")):
                        return False
                    if not self._knowledge_allowed(run, metadata):
                        return False
                    if metadata.get("artifact_id"):
                        # 历史产物继续派生时，撤销/过期同样传播；限制深度并拒绝环。
                        upstream = self.db.get(AgentArtifact, str(metadata["artifact_id"]))
                        upstream_run = self.db.get(AgentRun, upstream.run_id) if upstream else None
                        if not upstream or not upstream_run or upstream.user_id != run.user_id or upstream_run.project_id != run.project_id:
                            return False
                        if not self.reusable(upstream_run, upstream, max_age_hours=max_age_hours, _visited=visited, _budget=budget):
                            return False
            return True
        except (ArtifactStoreError, ValueError, TypeError, AttributeError, KeyError):
            return False

    def _file_allowed(self, run, file_id, expected_hash=None):
        from app.services.tools.providers.workspace_files import WorkspaceFileToolProvider
        item = self.db.get(ProjectFile, str(file_id), populate_existing=True)
        return bool(item and item.user_id == run.user_id and item.project_id == run.project_id
            and not WorkspaceFileToolProvider.is_sensitive_file_name(item.file_name)
            and (not expected_hash or hashlib.sha256((item.parsed_text or "").encode()).hexdigest() == expected_hash))

    def _knowledge_allowed(self, run, metadata):
        from app.models.knowledge import KnowledgeBase, KnowledgeChunk, KnowledgeDocument
        kb_id, doc_id, chunk_id = (metadata.get(key) for key in ("knowledge_base_id", "document_id", "chunk_id"))
        if not any((kb_id, doc_id, chunk_id)):
            return True
        chunk = self.db.get(KnowledgeChunk, str(chunk_id), populate_existing=True) if chunk_id else None
        doc = self.db.get(KnowledgeDocument, str(doc_id or (chunk.document_id if chunk else "")), populate_existing=True)
        kb = self.db.get(KnowledgeBase, str(kb_id or (doc.knowledge_base_id if doc else "")), populate_existing=True)
        if (not kb or kb.user_id != run.user_id or kb.project_id not in (None, run.project_id)
                or (doc_id and (not doc or doc.user_id != run.user_id or doc.knowledge_base_id != kb.id))):
            return False
        if chunk_id and (not chunk or chunk.user_id != run.user_id or chunk.knowledge_base_id != kb.id
                or chunk.index_generation != kb.active_index_generation or (doc and chunk.document_id != doc.id)):
            return False
        generation = metadata.get("index_generation")
        return not generation or generation == kb.active_index_generation
