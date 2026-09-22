from __future__ import annotations

"""同步 Chat 向低风险 Durable Tool Run 的显式交接。

预览是一个短时签名令牌，不写入数据库、更不会创建 Outbox。用户确认后才把
令牌中冻结的请求交给 DurableToolRunService.enqueue()，因此所有持久化安全
校验、幂等、lease 和 checkpoint 仍只由既有 Durable Runtime 负责。
"""

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.core.config import settings
from app.services.durable_tool_runtime import DurableToolRunService, DurableToolRuntimeError
from app.services.skill_catalog import SkillCatalog, SkillCatalogError
from app.services.tools.catalog import ToolCatalog


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class DurableHandoffError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class DurableHandoffPreview:
    handoff_token: str
    expires_at: datetime
    skill_key: str | None
    skill_display_name: str | None
    tool_calls: list[dict[str, Any]]
    max_attempts: int


class DurableHandoffService:
    """冻结用户确认前的低风险只读任务，不新增第二套 Durable 状态机。"""

    TOKEN_VERSION = 1
    TOKEN_TTL_SECONDS = 10 * 60

    def __init__(self, db: Session) -> None:
        self.db = db

    @staticmethod
    def _canonical(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")

    @staticmethod
    def _decode(value: str) -> bytes:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))

    @classmethod
    def _signature(cls, payload: str) -> str:
        # 复用服务端认证密钥，但使用 purpose 分隔，避免令牌可跨用途解释。
        secret = f"durable-handoff-v1:{settings.auth_secret_key}".encode("utf-8")
        return cls._encode(hmac.new(secret, payload.encode("ascii"), hashlib.sha256).digest())

    @classmethod
    def _sign(cls, payload: dict[str, Any]) -> str:
        encoded = cls._encode(cls._canonical(payload).encode("utf-8"))
        return f"{encoded}.{cls._signature(encoded)}"

    @classmethod
    def _verify(cls, token: str) -> dict[str, Any]:
        try:
            encoded, signature = str(token or "").split(".", 1)
            if not hmac.compare_digest(cls._signature(encoded), signature):
                raise ValueError("signature")
            payload = json.loads(cls._decode(encoded).decode("utf-8"))
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            raise DurableHandoffError("invalid_handoff_token", "任务确认令牌无效，请重新发起任务。") from exc
        if not isinstance(payload, dict) or payload.get("version") != cls.TOKEN_VERSION:
            raise DurableHandoffError("invalid_handoff_token", "任务确认令牌无效，请重新发起任务。")
        try:
            expires_at = datetime.fromtimestamp(int(payload["expires_at"]), tz=timezone.utc)
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            raise DurableHandoffError("invalid_handoff_token", "任务确认令牌无效，请重新发起任务。") from exc
        if expires_at <= utcnow():
            raise DurableHandoffError("handoff_expired", "任务确认已过期，请重新发起任务。")
        return payload

    def preview(
        self,
        *,
        user_id: str,
        project_id: str | None,
        conversation_id: str | None,
        assistant_message_id: str | None,
        calls: list[dict[str, Any]],
        skill_key: str | None,
        max_attempts: int,
    ) -> DurableHandoffPreview:
        """校验可见边界并签发确认令牌；这里绝不创建 AgentRun 或 Outbox。"""

        if not calls or len(calls) > DurableToolRunService.MAX_STEPS:
            raise DurableHandoffError("invalid_step_count", "可恢复任务需包含 1 到 12 个工具步骤。")
        try:
            DurableToolRunService(self.db)._validate_scope(
                user_id=user_id,
                project_id=project_id,
                conversation_id=conversation_id,
                assistant_message_id=assistant_message_id,
            )
        except DurableToolRuntimeError as exc:
            raise DurableHandoffError(exc.code, str(exc)) from exc

        skill_display_name: str | None = None
        allowed_tool_keys: set[str] | None = None
        if skill_key:
            try:
                skill = SkillCatalog().resolve_for_execution(
                    db=self.db,
                    user_id=user_id,
                    project_id=project_id,
                    skill_key=skill_key,
                )
            except SkillCatalogError as exc:
                raise DurableHandoffError("skill_not_ready", str(exc)) from exc
            if not skill.durable_eligible:
                raise DurableHandoffError("skill_not_durable", "该 Skill 不允许进入可恢复只读工作流。")
            skill_display_name = skill.display_name
            allowed_tool_keys = set(skill.allowed_tool_keys)

        catalog = ToolCatalog(db=self.db, user_id=user_id, project_id=project_id)
        summaries: list[dict[str, Any]] = []
        call_ids: set[str] = set()
        for index, raw in enumerate(calls, start=1):
            if not isinstance(raw, dict):
                raise DurableHandoffError("invalid_step", "每个工具步骤必须是对象。")
            call_id = str(raw.get("call_id") or f"step-{index}").strip()
            tool_key = str(raw.get("tool_key") or "").strip()
            if not call_id or len(call_id) > 64 or call_id in call_ids:
                raise DurableHandoffError("invalid_call_id", "工具步骤标识必须唯一且长度合法。")
            call_ids.add(call_id)
            definition = catalog.get_or_none(tool_key)
            if not definition:
                raise DurableHandoffError("unknown_tool", f"未找到工具：{tool_key or 'unknown'}")
            if not definition.read_only or definition.risk_level != "low":
                raise DurableHandoffError(
                    "unsafe_tool_not_supported",
                    f"{tool_key} 不是低风险只读工具，不能进入可恢复任务。",
                )
            if allowed_tool_keys is not None and tool_key not in allowed_tool_keys:
                raise DurableHandoffError("skill_scope_violation", f"{tool_key} 不在当前 Skill 的允许范围内。")
            depends_on = raw.get("depends_on") or []
            if not isinstance(depends_on, list) or not all(isinstance(item, str) and item for item in depends_on):
                raise DurableHandoffError("invalid_dependencies", f"{tool_key} 的依赖格式非法。")
            summaries.append(
                {
                    "call_id": call_id,
                    "tool_key": tool_key,
                    "display_name": definition.display_name,
                    "depends_on": list(depends_on),
                }
            )

        # 预览阶段也拒绝明显无效的依赖图，避免用户确认一个必然无法入队的计划。
        for item in summaries:
            dependencies = item["depends_on"]
            if any(dependency not in call_ids or dependency == item["call_id"] for dependency in dependencies):
                raise DurableHandoffError("invalid_dependencies", "依赖必须引用同一个任务中的其它步骤。")
        unresolved = {item["call_id"]: set(item["depends_on"]) for item in summaries}
        ready = [call_id for call_id, dependencies in unresolved.items() if not dependencies]
        resolved_count = 0
        while ready:
            current = ready.pop()
            resolved_count += 1
            for call_id, dependencies in unresolved.items():
                if current in dependencies:
                    dependencies.remove(current)
                    if not dependencies:
                        ready.append(call_id)
        if resolved_count != len(unresolved):
            raise DurableHandoffError("cyclic_dependencies", "可恢复任务不能包含循环依赖。")

        expires_at = utcnow() + timedelta(seconds=self.TOKEN_TTL_SECONDS)
        payload = {
            "version": self.TOKEN_VERSION,
            "handoff_id": secrets.token_urlsafe(18),
            "user_id": user_id,
            "project_id": project_id,
            "conversation_id": conversation_id,
            "assistant_message_id": assistant_message_id,
            "skill_key": skill_key,
            "max_attempts": max(1, min(int(max_attempts), 5)),
            # 冻结的是原始调用声明；确认时 Durable Runtime 再执行完整 schema/DAG 校验。
            "calls": calls,
            "expires_at": int(expires_at.timestamp()),
        }
        token = self._sign(payload)
        if len(token) > 24_000:
            raise DurableHandoffError("handoff_too_large", "任务参数过大，请拆分为多个较小的只读任务。")
        return DurableHandoffPreview(
            handoff_token=token,
            expires_at=expires_at,
            skill_key=skill_key,
            skill_display_name=skill_display_name,
            tool_calls=summaries,
            max_attempts=payload["max_attempts"],
        )

    def confirm(self, *, user_id: str, handoff_token: str):
        """确认冻结请求并原子入队；不能携带新的用户可控 calls。"""

        payload = self._verify(handoff_token)
        if not hmac.compare_digest(str(payload.get("user_id") or ""), str(user_id)):
            raise DurableHandoffError("handoff_scope_violation", "该任务确认不属于当前用户。")
        try:
            return DurableToolRunService(self.db).enqueue(
                user_id=user_id,
                project_id=payload.get("project_id"),
                conversation_id=payload.get("conversation_id"),
                assistant_message_id=payload.get("assistant_message_id"),
                calls=payload.get("calls") or [],
                idempotency_key=f"handoff:{payload.get('handoff_id')}",
                skill_key=payload.get("skill_key"),
                max_attempts=int(payload.get("max_attempts") or 3),
            )
        except DurableToolRuntimeError as exc:
            raise DurableHandoffError(exc.code, str(exc)) from exc
