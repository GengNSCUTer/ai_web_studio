"""单次工具执行共用内核；调用方持有业务锁并负责最终事务提交。

这里不领取任务、不扩大权限、不决定Chat是否继续规划，也不替代Executor。
Chat、固定Durable计划与文档阶段分别保留自己的租约校验和产品状态。
"""

from dataclasses import dataclass
import hashlib
from typing import Any

from app.models.agent_runtime import AgentArtifact
from app.services.runtime_lease import aware, utcnow
from app.services.tools.quality import is_usable_tool_result, quality_status_for_result
from app.services.tools.schemas import tool_execution_snapshot


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
