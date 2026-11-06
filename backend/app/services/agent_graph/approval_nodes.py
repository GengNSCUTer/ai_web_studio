"""文件审批的等待、授权恢复和结果复核图，绝不把确认令牌写入State。"""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass
import json
from typing import Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt
from langsmith import tracing_context
from psycopg import Error as PostgresError
from psycopg_pool import PoolTimeout
from sqlalchemy import select

from app.models.agent_runtime import AgentApproval, AgentRun, AgentStep
from app.services.approval_checkpoint_service import (
    APPROVAL_GRAPH_VERSION, ApprovalCheckpointConflict, ApprovalIdentity, approval_checkpointer,
)


class ApprovalState(TypedDict, total=False):
    approval_id: str
    run_id: str
    step_id: str
    state_version: int
    phase: str
    decision: str
    status: str


@dataclass
class ApprovalContext:
    service: Any
    identity: ApprovalIdentity
    decision: str | None = None
    token: str = ""
    result: Any = None
    error: Exception | None = None


def _wait_for_user(state: ApprovalState, runtime: Runtime[ApprovalContext]):
    # 进入节点前，PatchDraft/AgentApproval已以业务幂等键提交；重进不创建新草稿。
    decision = interrupt({"approval_id": state["approval_id"], "run_id": state["run_id"],
                          "state_version": state["state_version"], "action": "workspace.files.apply_edit"})
    if decision not in {"approve", "reject"} or decision != runtime.context.decision:
        raise ApprovalCheckpointConflict("图恢复缺少本次已认证的审批决定")
    return {"phase": "resumed", "decision": decision}


def _apply_decision(state: ApprovalState, runtime: Runtime[ApprovalContext]):
    context = runtime.context
    # 不信任checkpoint中的decision；只允许服务端当前请求的身份和决定驱动写入。
    if context.decision not in {"approve", "reject"} or state["state_version"] != context.identity.state_version:
        raise ApprovalCheckpointConflict("审批恢复版本或决定不一致")
    from app.services.agent_runtime_service import AgentRuntimeError
    try:
        if context.decision == "approve":
            context.result = context.service._apply_file_edit_business(
                approval_id=context.identity.approval_id, user_id=context.identity.user_id,
                approval_token=context.token, expected_state_version=state["state_version"])
        else:
            context.result = context.service._reject_file_edit_business(
                approval_id=context.identity.approval_id, user_id=context.identity.user_id,
                expected_state_version=state["state_version"])
    except AgentRuntimeError as exc:
        context.service.db.rollback()
        context.error = exc
        # 冲突/过期等可能已经提交业务终态；图只记录状态，不保留错误中的正文。
        return {"phase": "refused", "status": exc.code, "decision": context.decision}
    # 上次批准可能在提交前宕机。本次仍未落库时允许用户明确拒绝；以本次授权请求为准。
    return {"phase": "business_committed", "status": context.result.status, "decision": context.decision}


def _reconcile(state: ApprovalState, runtime: Runtime[ApprovalContext]):
    db = runtime.context.service.db
    db.expire_all()
    run = db.get(AgentRun, state["run_id"])
    status = run.status if run else "missing"
    db.commit()
    return {"phase": "reconciled", "status": status}


def build_approval_graph(checkpointer):
    graph = StateGraph(ApprovalState, context_schema=ApprovalContext)
    graph.add_node("wait_for_user", _wait_for_user)
    graph.add_node("apply_decision", _apply_decision)
    graph.add_node("reconcile", _reconcile)
    graph.add_edge(START, "wait_for_user")
    graph.add_edge("wait_for_user", "apply_decision")
    graph.add_edge("apply_decision", "reconcile")
    graph.add_edge("reconcile", END)
    return graph.compile(checkpointer=checkpointer, name="aiws_file_approval_v1")


def run_approval_graph(service, *, approval_id, user_id, decision=None, token="", expected_state_version=None):
    """只供已认证业务服务调用，不暴露任意thread_id/Command恢复API。"""
    from app.services.agent_runtime_service import AgentRuntimeError
    db = service.db
    approval = db.scalar(select(AgentApproval).where(AgentApproval.id == approval_id, AgentApproval.user_id == user_id)
                         .execution_options(populate_existing=True))
    run = db.get(AgentRun, approval.run_id) if approval else None
    step = db.get(AgentStep, approval.step_id) if approval else None
    if not approval or not run or not step or run.user_id != user_id or run.runtime_kind != "file_edit":
        raise AgentRuntimeError("approval_not_found", "审批不存在。")
    if json.loads(run.planner_state_json or "{}").get("runtime_engine") != APPROVAL_GRAPH_VERSION:
        raise AgentRuntimeError("unsupported_runtime_version", "不能使用未知审批运行时恢复文件写入。")
    identity = ApprovalIdentity(user_id, run.project_id, run.id, step.id, approval_id, run.state_version)
    if expected_state_version is not None and expected_state_version != identity.state_version:
        raise AgentRuntimeError("approval_version_conflict", "审批版本已变化，请重新查看提案。")
    if run.status != "waiting_approval" or step.status != "waiting_approval":
        raise AgentRuntimeError("approval_not_pending", "当前任务不再等待文件审批。")
    db.commit()
    url = db.get_bind().url
    persistent = url.get_backend_name() == "postgresql"
    saver_context = approval_checkpointer(url, identity) if persistent else nullcontext(InMemorySaver())
    context = ApprovalContext(service, identity, decision, token)
    try:
        with tracing_context(enabled=False), saver_context as saver:
            graph = build_approval_graph(saver)
            saved = graph.get_state(identity.config)
            # 与新检查点对照必须在同审批串行锁内。数据库终态优先，重复请求不再写文件。
            db.expire_all()
            current = db.get(AgentRun, identity.run_id)
            if current.state_version != identity.state_version or current.status != "waiting_approval":
                # 另一个确认已提交但本请求刚拿到串行锁时，复用业务结果，不再次修改。
                current_approval = db.get(AgentApproval, approval_id)
                if decision == "approve" and current_approval.status == "consumed":
                    return service._apply_file_edit_business(approval_id=approval_id, user_id=user_id,
                                                            approval_token=token)
                if decision == "reject" and current_approval.status == "rejected":
                    return service._reject_file_edit_business(approval_id=approval_id, user_id=user_id)
                db.rollback()
                raise AgentRuntimeError("approval_version_conflict", "审批已被其他请求处理，请刷新状态。")
            db.commit()
            if saved.values and saved.values.get("state_version") != identity.state_version:
                raise AgentRuntimeError("approval_version_conflict", "图中审批版本与当前业务版本不一致。")
            if not saved.values:
                graph.invoke({"approval_id": approval_id, "run_id": identity.run_id, "step_id": identity.step_id,
                              "state_version": identity.state_version, "phase": "waiting_approval"},
                             config=identity.config, context=context, durability="sync")
            if decision is not None:
                # 令牌、有效期、范围及当前权限在写resume前核验；写入节点还会再次核验。
                service._validate_file_edit_decision(identity, decision, token)
                db.commit()
                if not graph.get_state(identity.config).next:
                    raise AgentRuntimeError("approval_graph_not_waiting", "审批图当前不在等待状态。")
                graph.invoke(Command(resume=decision), config=identity.config, context=context, durability="sync")
    except ApprovalCheckpointConflict as exc:
        db.rollback()
        raise AgentRuntimeError("approval_version_conflict", str(exc)) from exc
    except AgentRuntimeError:
        db.rollback()
        raise
    except (PostgresError, PoolTimeout) as exc:
        db.rollback()
        # 图写入可能晚于文件事务，不能在此换旧引擎或宣称文件一定未修改。
        raise AgentRuntimeError("approval_checkpoint_unavailable",
            "审批恢复记录暂不可用，请刷新任务状态；系统不会自动重复执行写入。") from exc
    if context.error:
        raise context.error
    return context.result
