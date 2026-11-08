"""单次工具执行共用内核；调用方持有业务锁并负责最终事务提交。

这里不领取任务、不扩大权限、不决定Chat是否继续规划，也不替代Executor。
Chat、固定Durable计划与文档阶段分别保留自己的租约校验和产品状态。
"""

from dataclasses import dataclass
from contextvars import ContextVar
import hashlib
from typing import Any

from app.models.agent_runtime import AgentArtifact
from app.services.runtime_lease import aware, utcnow
from app.services.tools.quality import is_usable_tool_result, quality_status_for_result
from app.services.tools.schemas import tool_execution_snapshot


# Executor只依赖通用调用上下文；具体Chat/固定任务协调器决定何时绑定。
current_invocation_runtime = ContextVar("current_invocation_runtime", default=None)


class InvocationRuntimeError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class InvocationIdentity:
    """业务身份包含租户、运行与步骤，不能仅以模型生成的call_id判断复用。"""

    user_id: str
    project_id: str | None
    run_id: str
    step_id: str
    call_id: str
    tool_key: str
    arguments_hash: str

    @classmethod
    def from_step(cls, run, step):
        if not run.id or not run.user_id or not step.id or step.run_id != run.id:
            raise InvocationRuntimeError("invocation_scope_conflict", "调用步骤不属于当前运行。")
        return cls(run.user_id, run.project_id, run.id, step.id, step.call_id,
                   step.tool_key, step.arguments_hash)


@dataclass(frozen=True)
class InvocationRecoveryPolicy:
    """由服务端定义推导，外部响应/模型声明不能开放幂等写入或对账。"""

    retry_policy: str
    idempotency_policy: str
    reconciliation_policy: str

    @classmethod
    def for_tool(cls, definition):
        if definition.read_only and definition.risk_level == "low":
            return cls("bounded_read_only", "not_required", "reuse_committed_result")
        # 目前没有经过审核的外部幂等/对账写接口；不因添加分类就假装支持。
        return cls("never_automatically", "unsupported", "manual_confirmation")

    @property
    def allows_unknown_result_retry(self):
        return self.retry_policy == "bounded_read_only"


@dataclass(frozen=True)
class InvocationOutcome:
    step_status: str
    result_status: str
    quality_status: str
    result_semantics: str
    dependency_ready: bool

    @classmethod
    def from_result(cls, result):
        quality = quality_status_for_result(result)[0]
        usable = is_usable_tool_result(result)
        semantics = str(getattr(result, "result_semantics", "evidence") or "evidence")
        if result.status == "confirmation_required":
            status = "waiting_approval"
        elif usable:
            status = "succeeded"
        elif result.status == "success" and semantics == "approval_draft" and quality == "valid":
            status = "preview"
        else:
            status = "failed"
        return cls(status, result.status, quality, semantics, usable)


@dataclass(frozen=True)
class InvocationRecoveryDecision:
    action: str
    code: str
    reason: str


class ToolInvocationRuntime:
    """协调器共享的身份、尝试和产物提交协议，无独立commit与网络重试。"""

    @staticmethod
    def verify_snapshot(definition, stored_snapshot):
        if not isinstance(stored_snapshot, dict) or not stored_snapshot.get("fingerprint"):
            raise InvocationRuntimeError("tool_snapshot_missing", "调用缺少工具执行快照。")
        if stored_snapshot != tool_execution_snapshot(definition):
            raise InvocationRuntimeError("tool_snapshot_mismatch", "工具定义或安全约束已变化。")

    @staticmethod
    def validate_artifact(run, step, artifact):
        identity = InvocationIdentity.from_step(run, step)
        if (artifact.run_id != identity.run_id or artifact.step_id != identity.step_id
                or artifact.user_id != identity.user_id):
            raise InvocationRuntimeError("artifact_scope_conflict", "工具产物不属于当前用户、运行和步骤。")
        return identity

    @classmethod
    def reconcile(cls, run, step, definition, *, artifact=None, artifact_expected=False,
                  active_owner=None, active_version=None):
        """基于当前业务事实作恢复决定；调用方须先复核租约与当前授权。"""
        InvocationIdentity.from_step(run, step)
        if definition is None:
            return InvocationRecoveryDecision("block", "tool_missing", "工具已不可用，不能恢复或复用旧结果。")
        if artifact is not None:
            cls.validate_artifact(run, step, artifact)
            cls.verify_artifact_content(artifact)
            return InvocationRecoveryDecision("reuse", "committed_result", "复用已提交结果快照。")
        if artifact_expected or step.status == "succeeded":
            return InvocationRecoveryDecision("block", "committed_result_missing", "已提交步骤缺少对应结果，请先对账。")
        if int(step.attempts or 0) > 0 and not InvocationRecoveryPolicy.for_tool(definition).allows_unknown_result_retry:
            return InvocationRecoveryDecision("block", "side_effect_unknown", "该步骤的副作用结果未确认，已停止自动重放，请核实审批或产物。")
        if (active_owner is not None and step.status == "running" and step.attempts
                and step.lease_owner == active_owner and step.lease_version == active_version):
            return InvocationRecoveryDecision("block", "invocation_inflight", "同一执行权下已有相同调用正在执行，不能并发重复发送。")
        if int(step.attempts or 0) >= int(step.max_attempts or 0):
            return InvocationRecoveryDecision("block", "attempts_exhausted", "该步骤已达到恢复尝试上限。")
        return InvocationRecoveryDecision("execute", "new_or_retryable", "允许在当前预算内执行。")

    @staticmethod
    def verify_artifact_content(artifact):
        if hashlib.sha256(artifact.content_json.encode("utf-8")).hexdigest() != artifact.content_hash:
            raise InvocationRuntimeError("artifact_integrity_conflict", "已保存产物内容校验失败，不能继续复用。")

    @staticmethod
    def reuse_allowed(db, executor, resolver, definition, call):
        """复用同样受当前动作范围、项目配置、凭据与文件ACL约束。"""
        from app.models.agent_runtime import AgentRun
        from app.models.project_file import ProjectFile
        from app.services.tools.providers.workspace_files import WorkspaceFileToolProvider
        if not definition.enabled_by_default or not executor.action_guard.check(definition, call.arguments).allowed:
            return False
        if not definition.read_only and resolver.get_workspace_permission_mode(project_id=executor.project_id) == "read_only":
            return False
        if not resolver.is_tool_enabled_for_workspace(project_id=executor.project_id, tool_key=call.tool_key):
            return False
        if definition.credential_required or definition.credential_optional:
            credential = resolver.resolve(user_id=executor.user_id, provider_key=definition.credential_provider)
            if not credential.is_enabled and not (definition.credential_optional and credential.source in {"missing", "env_fallback_disabled"}):
                return False
        if call.arguments.get("file_id"):
            item = db.get(ProjectFile, str(call.arguments["file_id"]), populate_existing=True)
            if (not item or item.user_id != executor.user_id or item.project_id != executor.project_id
                    or WorkspaceFileToolProvider.is_sensitive_file_name(item.file_name)):
                return False
        if call.arguments.get("artifact_id"):
            item = db.get(AgentArtifact, str(call.arguments["artifact_id"]), populate_existing=True)
            run = db.get(AgentRun, item.run_id) if item else None
            if not item or not run or item.user_id != executor.user_id or run.user_id != executor.user_id or run.project_id != executor.project_id:
                return False
        return True

    @staticmethod
    def start_attempt(run, step, *, owner, lease_version, lease_expires_at):
        identity = InvocationIdentity.from_step(run, step)
        if (not owner or lease_version < 1 or lease_expires_at is None
                or aware(lease_expires_at) <= utcnow()):
            raise InvocationRuntimeError("invalid_invocation_lease", "调用缺少有效执行权。")
        if step.status not in {"planned", "pending", "running"}:
            raise InvocationRuntimeError("invocation_already_terminal", "调用已收口，不能重新开始。")
        if int(step.attempts or 0) >= int(step.max_attempts or 0):
            raise InvocationRuntimeError("attempts_exhausted", "调用已达到最大执行尝试次数。")
        now = utcnow()
        step.attempts = int(step.attempts or 0) + 1
        step.status, step.lease_owner, step.lease_version = "running", owner, lease_version
        step.started_at, step.heartbeat_at = step.started_at or now, now
        step.lease_expires_at = lease_expires_at
        return identity

    @classmethod
    def create_artifact(cls, db, *, run, step, content_json, artifact_type="tool_result",
                        preview="", max_chars=None, max_bytes=None, prompt_state="stored"):
        """调用方已核对当前租约；产物与Step/事件状态必须在同一事务提交。"""
        InvocationIdentity.from_step(run, step)
        if not isinstance(content_json, str):
            raise InvocationRuntimeError("artifact_serialization_failed", "产物必须是已安全编码的JSON文本。")
        if ((max_chars is not None and len(content_json) > max_chars)
                or (max_bytes is not None and len(content_json.encode("utf-8")) > max_bytes)):
            raise InvocationRuntimeError("artifact_too_large", "产物超过持久化大小限制。")
        artifact = AgentArtifact(run_id=run.id, step_id=step.id, user_id=run.user_id,
            artifact_type=artifact_type, content_json=content_json,
            content_hash=hashlib.sha256(content_json.encode("utf-8")).hexdigest(),
            preview=preview, char_count=len(content_json), prompt_state=prompt_state)
        db.add(artifact)
        db.flush()
        return artifact

    @staticmethod
    def result_payload(call, result, events) -> dict[str, Any]:
        """供业务产物展示/绑定的通用脱敏表示，恢复的类型编码由协调器选择。"""
        return {"call_id": call.call_id, "tool_key": call.tool_key,
            "sources": [source.to_public_dict() for source in result.sources],
            "events": [event.to_public_dict() for event in events], "elapsed_ms": result.elapsed_ms,
            "quality_status": quality_status_for_result(result)[0],
            "quality_reasons": list(getattr(result, "quality_reasons", []) or [])[:8],
            "quality_metadata": dict(getattr(result, "quality_metadata", {}) or {}),
            "result_semantics": str(getattr(result, "result_semantics", "evidence") or "evidence")}
