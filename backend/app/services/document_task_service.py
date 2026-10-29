"""把文档取证、模型草稿、导出和交付接到已有 Run/Step/Artifact。"""

from __future__ import annotations

import base64
from datetime import timedelta
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
from tempfile import TemporaryDirectory, NamedTemporaryFile
from uuid import uuid5, NAMESPACE_URL

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.agent_runtime import AgentApproval, AgentArtifact, AgentCheckpoint, AgentOutboxEvent, AgentRun, AgentStep
from app.models.conversation import Conversation
from app.models.message import Message
from app.models.project import Project
from app.models.user import User
from app.models.project_file import ProjectFile
from app.models.tool_config import WorkspaceAgentPolicy
from app.repositories.message_repo import MessageRepository
from app.schemas.document_task import DocumentTaskCreate
from app.services.agent_runtime_service import AgentRuntimeService
from app.services.document_delivery_service import DocumentDeliveryService
from app.services.durable_tool_runtime import DurableToolRunService, utcnow
from app.services.tools.providers.workspace_files import WorkspaceFileToolProvider


KIND = "document_workflow"
STAGES = ("evidence", "draft", "export", "delivery")
TERMINAL = {"succeeded", "failed", "cancelled", "dead_letter"}
MAX_EVIDENCE_CHARS = 40000
MAX_DRAFT_CHARS = 100000


class DocumentTaskError(ValueError):
    pass


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def digest(value):
    return hashlib.sha256(encode(value).encode()).hexdigest()


class DocumentTaskService:
    def __init__(self, db: Session):
        self.db = db

    def get(self, user_id, run_id, *, lock=False):
        query = select(AgentRun).where(AgentRun.id == run_id, AgentRun.user_id == user_id, AgentRun.runtime_kind == KIND)
        if lock:
            query = query.execution_options(populate_existing=True).with_for_update()
        run = self.db.scalar(query)
        if not run or not self.db.scalar(select(Project.id).where(Project.id == run.project_id, Project.user_id == user_id)):
            raise DocumentTaskError("任务不存在或无权访问。")
        return run

    def create(self, user_id, payload: DocumentTaskCreate):
        self.db.scalar(select(User).where(User.id == user_id).with_for_update())
        project = self.db.scalar(select(Project).where(Project.id == payload.project_id, Project.user_id == user_id).with_for_update())
        if not project:
            raise DocumentTaskError("工作区不存在或无权访问。")
        AgentRuntimeService(self.db)._validate_context_scope(user_id=user_id, project_id=payload.project_id,
            conversation_id=payload.conversation_id, assistant_message_id=payload.assistant_message_id)
        if not payload.file_ids and not payload.assistant_message_id:
            raise DocumentTaskError("至少选择一份资料或已完成的助手回答。")
        if Path(payload.file_name).name != payload.file_name or "\\" in payload.file_name or any(ord(x) < 32 for x in payload.file_name):
            raise DocumentTaskError("文件名不能包含目录、反斜杠或控制字符。")
        request = payload.model_dump(exclude={"idempotency_key"})
        request["file_name"] = DocumentDeliveryService._safe_file_name(payload.file_name, payload.format)
        request["file_ids"] = list(dict.fromkeys(payload.file_ids))
        source_message = self.answer(user_id, request) if payload.assistant_message_id else None
        request["source_generation_id"] = source_message.generation_id if source_message else None
        key = f"document:{user_id}:{payload.idempotency_key}" + (f":{source_message.generation_id}" if source_message else "")
        existing = self.db.scalar(select(AgentRun).where(AgentRun.idempotency_key == key))
        if existing:
            if json.loads(existing.input_json) != request:
                raise DocumentTaskError("幂等键已用于不同文档任务。")
            return self.snapshot(user_id, existing.id)
        count = self.db.scalar(select(func.count()).select_from(AgentRun).where(
            AgentRun.user_id == user_id, AgentRun.runtime_kind == KIND, AgentRun.status.in_(["queued", "running", "waiting_approval"])))
        if count >= 10:
            raise DocumentTaskError("最多保留 10 个未完成文档任务，请先完成或取消。")
        # 创建时先核验所有资料；执行取证时仍重新读取归属与内容。
        for file_id in request["file_ids"]:
            self.file(user_id, payload.project_id, file_id)
        if payload.assistant_message_id:
            self.answer(user_id, request)
        run = AgentRun(user_id=user_id, project_id=project.id, conversation_id=payload.conversation_id,
            assistant_message_id=payload.assistant_message_id, runtime_kind=KIND, status="queued",
            input_json=encode(request), planner_state_json="{}", idempotency_key=key, max_steps=4, current_step=0)
        self.db.add(run)
        self.db.flush()
        for sequence, stage in enumerate(STAGES, 1):
            step = AgentStep(run_id=run.id, sequence=sequence, call_id=stage, tool_key=f"documents.{stage}",
                arguments_json="{}", arguments_hash=digest({"request": request, "stage": stage}),
                status="pending" if sequence == 1 else "planned", max_attempts=2, depends_on_json=encode([STAGES[sequence - 2]] if sequence > 1 else []))
            self.db.add(step)
            self.db.flush()
            if sequence == 1:
                self.enqueue(run, step)
        self.checkpoint(run, None, "created")
        self.db.commit()
        return self.snapshot(user_id, run.id)

    def file(self, user_id, project_id, file_id):
        item = self.db.scalar(select(ProjectFile).where(ProjectFile.id == file_id, ProjectFile.user_id == user_id,
                                                       ProjectFile.project_id == project_id))
        if not item or WorkspaceFileToolProvider.is_sensitive_file_name(item.file_name):
            raise DocumentTaskError("授权资料不存在、跨工作区或属于敏感文件。")
        if not (item.parsed_text or "").strip():
            raise DocumentTaskError("文件没有可用正文。")
        return item

    def answer(self, user_id, request):
        conversation = self.db.get(Conversation, request["conversation_id"])
        message = self.db.get(Message, request["assistant_message_id"])
        if (not conversation or conversation.user_id != user_id or conversation.project_id != request["project_id"]
                or not message or message.conversation_id != conversation.id or message.role != "assistant"
                or message.status != "done" or not message.content.strip()):
            raise DocumentTaskError("只能使用当前工作区已完成的助手回答。")
        return message

    def collect(self, run):
        request = json.loads(run.input_json)
        records, total = [], 0
        for file_id in request["file_ids"]:
            item = self.file(run.user_id, run.project_id, file_id)
            text = item.parsed_text
            total += len(text)
            records.append({"id": item.id, "title": item.file_name, "content": text, "kind": "project_file"})
        if request["assistant_message_id"]:
            message = self.answer(run.user_id, request)
            if message.generation_id != request.get("source_generation_id"):
                raise DocumentTaskError("来源回答已重新生成，请基于新版本创建文档任务。")
            total += len(message.content)
            records.append({"id": message.id, "title": "会话整理结果", "content": message.content,
                            "kind": "assistant_answer", "original_sources": json.loads(message.external_sources or "[]")})
        if total > MAX_EVIDENCE_CHARS:
            raise DocumentTaskError("授权正文超过 40,000 字符，请缩小文件范围；不会静默截断证据。")
        references = [f"{x['kind']}:{x['id']} / {x['title']}" for x in records]
        for item in records:
            for source in (item.get("original_sources") or [])[:30]:
                if isinstance(source, dict):
                    meta = source.get("metadata") or {}
                    references.append(" / ".join(str(x) for x in [source.get("citation_label"), source.get("title"),
                        source.get("url"), f"chunk:{meta['chunk_id']}" if isinstance(meta, dict) and meta.get("chunk_id") else None] if x)[:2000])
        return {"sources": records, "references": list(dict.fromkeys(references))}

    def enqueue(self, run, step):
        key = f"document-stage:{run.id}:{step.id}:{run.state_version}"
        self.db.add(AgentOutboxEvent(run_id=run.id, step_id=step.id, event_key=key,
                    event_type="document_stage.requested", payload_json="{}", status="pending", available_at=utcnow()))

    def checkpoint(self, run, step, event):
        self.db.add(AgentCheckpoint(run_id=run.id, step_sequence=step.sequence if step else 0,
            state_version=run.state_version, planner_state_json=run.planner_state_json,
            observations_json=encode([{"event": event}]), remaining_budget_json="{}"))

    def artifact(self, run_id, stage):
        artifact = self.db.scalar(select(AgentArtifact).join(AgentStep, AgentStep.id == AgentArtifact.step_id)
            .where(AgentArtifact.run_id == run_id, AgentStep.call_id == stage).order_by(AgentArtifact.created_at.desc()).limit(1))
        if not artifact:
            raise DocumentTaskError("上一阶段产物缺失。")
        return artifact, json.loads(artifact.content_json)

    def save_stage(self, run, step, event, result):
        payload = encode(result)
        if len(payload) > 2_000_000:
            raise DocumentTaskError("阶段产物超过持久化限额。")
        artifact = AgentArtifact(run_id=run.id, step_id=step.id, user_id=run.user_id,
            artifact_type=f"document_{step.call_id}", content_hash=hashlib.sha256(payload.encode()).hexdigest(),
            preview=(result.get("text") or result.get("file_name") or f"{step.call_id} 阶段完成")[:3000],
            content_json=payload, char_count=len(payload))
        self.db.add(artifact)
        self.db.flush()
        step.status, step.result_json, step.finished_at = "succeeded", encode({"artifact_id": artifact.id}), utcnow()
        step.lease_owner, step.lease_expires_at = None, None
        event.status, event.lease_owner, event.lease_expires_at = "succeeded", None, None
        run.state_version += 1
        run.current_step = step.sequence
        self.db.flush()
        if step.sequence < 4:
            next_step = self.db.scalar(select(AgentStep).where(AgentStep.run_id == run.id, AgentStep.sequence == step.sequence + 1))
            if step.sequence == 3:
                # 不占 Worker、不保持租约或数据库事务来等待用户。
                next_step.status, run.status = "waiting_approval", "waiting_approval"
                self.db.add(AgentApproval(run_id=run.id, step_id=next_step.id, user_id=run.user_id,
                    action="documents.save_to_workspace", arguments_hash=next_step.arguments_hash,
                    status="pending", expires_at=utcnow() + timedelta(hours=24)))
                self.project_message(run, "文档已导出，等待确认保存", "可预览下载；尚未写入工作区。")
            else:
                run.status = "queued"
                next_step.status = "pending"
                self.enqueue(run, next_step)
        else:
            run.status, run.finished_at = "succeeded", utcnow()
            self.project_message(run, "文档任务已完成", f"已交付：{result['file_name']}")
        self.checkpoint(run, step, f"{step.call_id}_succeeded")

    def snapshot(self, user_id, run_id):
        run = self.get(user_id, run_id)
        artifacts = list(self.db.scalars(select(AgentArtifact).where(AgentArtifact.run_id == run.id, AgentArtifact.user_id == user_id)
                                           .order_by(AgentArtifact.created_at)))
        state = json.loads(run.planner_state_json)
        return {"id": run.id, "project_id": run.project_id, "conversation_id": run.conversation_id,
                "status": run.status, "current_step": run.current_step, "file_name": json.loads(run.input_json)["file_name"],
                "result_message_id": state.get("document_result_message_id"),
                "steps": [{"id": s.id, "stage": s.call_id, "status": s.status, "attempts": s.attempts,
                           "error_code": s.error_code, "error_message": s.error_message}
                          for s in self.db.scalars(select(AgentStep).where(AgentStep.run_id == run.id).order_by(AgentStep.sequence))],
                "artifacts": [{"id": a.id, "stage": a.artifact_type, "preview": a.preview,
                              "download_url": f"/api/document-tasks/{run.id}/artifacts/{a.id}/download" if a.artifact_type == "document_export" else None}
                              for a in artifacts],
                "approvals": [{"id": a.id, "status": a.status, "action": a.action} for a in self.db.scalars(
                    select(AgentApproval).where(AgentApproval.run_id == run.id))]}

    def approve(self, user_id, approval_id, token):
        # 与 Worker 保持 event→run→step 的写入顺序；审批不能锁完对象再等待网络。
        approval = self.db.scalar(select(AgentApproval).where(AgentApproval.id == approval_id, AgentApproval.user_id == user_id))
        if not approval or approval.action != "documents.save_to_workspace":
            raise DocumentTaskError("文档审批不存在。")
        run = self.get(user_id, approval.run_id, lock=True)
        self.db.refresh(approval, with_for_update=True)
        step = self.db.get(AgentStep, approval.step_id)
        if approval.status == "consumed":
            return self.snapshot(user_id, run.id)
        expiry = approval.expires_at.replace(tzinfo=approval.expires_at.tzinfo or utcnow().tzinfo)
        if (approval.status != "pending" or run.status != "waiting_approval" or step.status != "waiting_approval"
                or expiry <= utcnow() or not approval.nonce_hash
                or not hmac.compare_digest(approval.nonce_hash, hashlib.sha256(token.encode()).hexdigest())
                or approval.arguments_hash != step.arguments_hash):
            raise DocumentTaskError("审批已过期、被取消或确认挑战不匹配。")
        policy = self.db.get(WorkspaceAgentPolicy, run.project_id)
        if policy and policy.permission_mode == "read_only":
            raise DocumentTaskError("工作区只读，不能批准保存文档。")
        approval.status, approval.consumed_at, approval.nonce_hash = "consumed", utcnow(), None
        step.status, run.status = "pending", "queued"
        run.state_version += 1
        self.enqueue(run, step)
        self.checkpoint(run, step, "delivery_approved")
        self.db.commit()
        return self.snapshot(user_id, run.id)

    def cancel(self, user_id, run_id):
        self.get(user_id, run_id)
        # 先锁活动事件，避免与 Worker 写回的事件行锁顺序相反。
        events = list(self.db.scalars(select(AgentOutboxEvent).where(AgentOutboxEvent.run_id == run_id)
                                      .order_by(AgentOutboxEvent.id).with_for_update()))
        run = self.get(user_id, run_id, lock=True)
        if run.status in TERMINAL:
            return self.snapshot(user_id, run_id)
        run.status, run.finished_at = "cancelled", utcnow()
        run.state_version += 1
        for event in events:
            if event.status in {"pending", "running"}:
                event.status, event.lease_owner, event.lease_expires_at = "cancelled", None, None
        for step in self.db.scalars(select(AgentStep).where(AgentStep.run_id == run.id)):
            if step.status not in TERMINAL:
                step.status, step.finished_at = "cancelled", utcnow()
                step.lease_owner, step.lease_expires_at = None, None
        for approval in self.db.scalars(select(AgentApproval).where(AgentApproval.run_id == run.id, AgentApproval.status == "pending")):
            approval.status, approval.nonce_hash = "rejected", None
        self.project_message(run, "文档任务已取消", "已完成产物仍可打开下载；未完成阶段不会继续写入。")
        self.checkpoint(run, None, "cancelled")
        self.db.commit()
        return self.snapshot(user_id, run_id)

    def resume(self, user_id, run_id):
        run = self.get(user_id, run_id, lock=True)
        if run.status not in {"failed", "dead_letter"}:
            raise DocumentTaskError("仅失败任务可由用户显式重试；已完成阶段不会重做。")
        failed = self.db.scalar(select(AgentStep).where(AgentStep.run_id == run.id,
            AgentStep.status.in_(["failed", "dead_letter"])).order_by(AgentStep.sequence).limit(1))
        if not failed:
            raise DocumentTaskError("未找到可重试阶段。")
        failed.status, failed.attempts, failed.error_code, failed.error_message = "pending", 0, None, None
        run.status, run.finished_at = "queued", None
        run.state_version += 1
        self.enqueue(run, failed)
        self.checkpoint(run, failed, "user_retry")
        self.db.commit()
        return self.snapshot(user_id, run_id)

    def project_message(self, run, title, body):
        if not run.conversation_id:
            return
        conversation = self.db.scalar(select(Conversation).where(Conversation.id == run.conversation_id,
                    Conversation.user_id == run.user_id).with_for_update())
        if not conversation:
            return
        state = json.loads(run.planner_state_json)
        message = self.db.get(Message, state.get("document_result_message_id")) if state.get("document_result_message_id") else None
        content = f"{title}\n\n{body}\n\n[打开文档任务与产物](/documents?run={run.id})"
        sources = encode([{"source_type": "document_task", "provider": "document_runtime", "title": title,
                           "display_text": body, "metadata": {"run_id": run.id}}])
        if message and message.conversation_id == conversation.id:
            MessageRepository(self.db).invalidate_summary_for_changes(conversation, [message.id])
            message.content, message.external_sources = content, sources
            message.status = "failed" if run.status in {"failed", "dead_letter"} else "done"
        else:
            message = Message(conversation_id=conversation.id, role="assistant", content=content,
                              external_sources=sources, status="failed" if run.status in {"failed", "dead_letter"} else "done")
            MessageRepository(self.db).create(message)
        state["document_result_message_id"] = message.id
        run.planner_state_json = encode(state)

    def export(self, run):
        _, draft = self.artifact(run.id, "draft")
        request = json.loads(run.input_json)
        text = DocumentDeliveryService._markdown_with_sources(draft["text"], draft["references"])
        if request["format"] == "docx":
            with TemporaryDirectory(prefix="aiws-doc-export-") as tmp:
                path = Path(tmp) / "draft.docx"
                DocumentDeliveryService._render_docx(draft["text"], draft["references"], path)
                data = path.read_bytes()
            mime = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        else:
            data, mime = text.encode(), "text/markdown; charset=utf-8"
        if len(data) > 1_000_000:
            raise DocumentTaskError("导出文件超出 1 MB 限制。")
        return {"file_name": request["file_name"], "mime_type": mime, "data_base64": base64.b64encode(data).decode(),
                "text": text, "references": draft["references"]}

    def deliver(self, run, step):
        approval = self.db.scalar(select(AgentApproval).where(AgentApproval.step_id == step.id))
        if not approval or approval.status != "consumed" or approval.arguments_hash != step.arguments_hash:
            raise DocumentTaskError("保存未获得用户批准。")
        policy = self.db.get(WorkspaceAgentPolicy, run.project_id)
        if policy and policy.permission_mode == "read_only":
            raise DocumentTaskError("工作区已改为只读，拒绝写入。")
        _, exported = self.artifact(run.id, "export")
        data = base64.b64decode(exported["data_base64"], validate=True)
        file_id = str(uuid5(NAMESPACE_URL, f"aiws-document-delivery:{run.id}"))
        existing = self.db.get(ProjectFile, file_id)
        if existing:
            if existing.user_id != run.user_id or existing.project_id != run.project_id:
                raise DocumentTaskError("交付记录归属不一致。")
            return {"file_id": existing.id, "file_name": existing.file_name}
        root = Path(settings.upload_dir).resolve()
        root.mkdir(parents=True, exist_ok=True)
        directory = root / run.user_id / "project_documents" / run.project_id
        # 目录逃逸和预置符号链接必须在写入前拒绝，不能仅检查最终 resolve 后的路径。
        parent = root
        for part in (run.user_id, "project_documents", run.project_id):
            if not re.fullmatch(r"[a-zA-Z0-9_-]+", part):
                raise DocumentTaskError("文件归属标识非法。")
            parent = parent / part
            if parent.is_symlink():
                raise DocumentTaskError("交付目录包含符号链接。")
            parent.mkdir(exist_ok=True)
        storage_name = f"document-run-{run.id}{Path(exported['file_name']).suffix}"
        target = directory / storage_name
        if target.is_symlink():
            raise DocumentTaskError("产物路径包含符号链接。")
        with NamedTemporaryFile(dir=directory, prefix=".document.", delete=False) as tmp:
            tmp.write(data)
            temp = Path(tmp.name)
        try:
            os.replace(temp, target)
        finally:
            temp.unlink(missing_ok=True)
        item = ProjectFile(id=file_id, project_id=run.project_id, user_id=run.user_id, kind="generated_document",
            file_name=exported["file_name"], mime_type=exported["mime_type"], file_size=len(data), parsed_text=exported["text"],
            storage_key=str(target.relative_to(root)))
        self.db.add(item)
        self.db.flush()
        return {"file_id": item.id, "file_name": item.file_name}
