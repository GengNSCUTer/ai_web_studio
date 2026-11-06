"""可选文档保存审批图；只消费真实批准并排队，文件写入仍由Worker重新核验。"""

from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import StateGraph, START, END
from langgraph.runtime import Runtime
from langgraph.types import Command, interrupt
from langsmith import tracing_context
from psycopg import Error as PostgresError
from psycopg_pool import PoolTimeout
from sqlalchemy import select

from app.models.agent_runtime import AgentApproval
from app.services.approval_checkpoint_service import (
    ApprovalIdentity, ApprovalPostgresSaver, ApprovalCheckpointConflict, approval_checkpointer,
)


@dataclass(frozen=True)
class DocumentApprovalIdentity(ApprovalIdentity):
    @property
    def thread_id(self):
        return f"langgraph_document_approval_v1:u:{self.user_id}:p:{self.project_id}:r:{self.run_id}:s:{self.step_id}"


class DocumentApprovalSaver(ApprovalPostgresSaver):
    def _verify(self):
        item = self.identity
        row = self.conn.execute("""SELECT r.state_version,r.status AS run_status,a.status AS approval_status,s.status AS step_status
            FROM public.agent_runs r JOIN public.agent_steps s ON s.run_id=r.id
            JOIN public.agent_approvals a ON a.run_id=r.id AND a.step_id=s.id
            WHERE r.id=%s AND s.id=%s AND a.id=%s AND r.user_id=%s AND a.user_id=%s AND r.project_id=%s
              AND r.runtime_kind='document_workflow' AND a.action='documents.save_to_workspace' FOR UPDATE OF r""",
            (item.run_id, item.step_id, item.approval_id, item.user_id, item.user_id, item.project_id)).fetchone()
        if not row:
            raise ApprovalCheckpointConflict("文档保存审批范围失效")
        pending = (row["state_version"] == item.state_version and row["run_status"] == row["step_status"] == "waiting_approval"
                   and row["approval_status"] in {"pending", "expired"})
        # 消费批准后Worker可立即领取甚至完成；该不可逆审批事实允许补齐仅含ID的记录。
        consumed = (row["state_version"] >= item.state_version + 1 and row["approval_status"] == "consumed"
                    and row["run_status"] in {"queued", "running", "succeeded", "failed", "dead_letter"})
        rejected = (row["state_version"] == item.state_version + 1 and row["approval_status"] == "rejected"
                    and row["run_status"] == "succeeded" and row["step_status"] == "planned")
        if not (pending or consumed or rejected):
            raise ApprovalCheckpointConflict("文档保存审批版本已失效")


class SaveState(TypedDict, total=False):
    run_id: str
    approval_id: str
    state_version: int
    decision: str
    phase: str


@dataclass
class SaveContext:
    service: Any
    user_id: str
    approval_id: str
    decision: str | None = None
    token: str = ""
    result: Any = None


def _wait(state: SaveState, runtime: Runtime[SaveContext]):
    decision = interrupt({"approval_id": state["approval_id"], "run_id": state["run_id"],
                          "state_version": state["state_version"], "action": "documents.save_to_workspace"})
    if decision not in {"approve", "reject"} or decision != runtime.context.decision:
        raise ApprovalCheckpointConflict("文档恢复缺少本次用户决定")
    return {"decision": decision, "phase": "resumed"}


def _decide(state: SaveState, runtime: Runtime[SaveContext]):
    context = runtime.context
    context.service._validate_save_decision(context.user_id, context.approval_id,
        context.decision, context.token, state["state_version"])
    if context.decision == "approve":
        context.result = context.service._approve_workspace_save_business(context.user_id, context.approval_id,
            context.token, expected_state_version=state["state_version"])
    else:
        context.result = context.service._reject_workspace_save_business(context.user_id, context.approval_id,
            expected_state_version=state["state_version"])
    # snapshot会重新开启只读事务；返回框架前释放，不持有事务等待检查点。
    context.service.db.commit()
    return {"decision": context.decision, "phase": "decision_committed"}


def build_document_approval_graph(saver):
    graph = StateGraph(SaveState, context_schema=SaveContext)
    graph.add_node("wait_for_user", _wait)
    graph.add_node("consume_decision", _decide)
    graph.add_edge(START, "wait_for_user")
    graph.add_edge("wait_for_user", "consume_decision")
    graph.add_edge("consume_decision", END)
    return graph.compile(checkpointer=saver, name="aiws_document_save_approval_v1")


def run_document_approval_graph(service, user_id, approval_id, *, decision=None, token="", expected_state_version=None):
    from app.services.document_task_service import DocumentTaskError
    db = service.db
    approval = db.scalar(select(AgentApproval).where(AgentApproval.id == approval_id, AgentApproval.user_id == user_id)
                         .execution_options(populate_existing=True))
    if not approval or approval.action != "documents.save_to_workspace":
        raise DocumentTaskError("文档审批不存在。")
    run = service.get(user_id, approval.run_id)
    identity = DocumentApprovalIdentity(user_id, run.project_id, run.id, approval.step_id, approval_id, run.state_version)
    if expected_state_version is not None and expected_state_version != identity.state_version:
        raise DocumentTaskError("文档保存审批版本已变化。")
    db.commit()
    url = db.get_bind().url
    manager = (approval_checkpointer(url, identity, DocumentApprovalSaver) if url.get_backend_name() == "postgresql"
               else nullcontext(InMemorySaver()))
    context = SaveContext(service, user_id, approval_id, decision, token)
    try:
        with tracing_context(enabled=False), manager as saver:
            # 同审批锁内重新读取，另一个确认已提交时只返回业务结果。
            db.expire_all()
            approval = db.get(AgentApproval, approval_id)
            if decision == "approve" and approval.status == "consumed":
                return service._approve_workspace_save_business(user_id, approval_id, token)
            if decision == "reject" and approval.status == "rejected":
                return service._reject_workspace_save_business(user_id, approval_id)
            current = service.get(user_id, identity.run_id)
            if current.state_version != identity.state_version or current.status != "waiting_approval":
                raise DocumentTaskError("文档保存审批已被其他请求处理。")
            db.commit()
            graph = build_document_approval_graph(saver)
            saved = graph.get_state(identity.config)
            if not saved.values or (decision is None and saved.values.get("state_version") != identity.state_version):
                graph.invoke({"approval_id": approval_id, "run_id": identity.run_id,
                              "state_version": identity.state_version, "phase": "waiting_approval"},
                             context=context, config=identity.config, durability="sync")
            elif saved.values.get("state_version") != identity.state_version:
                raise DocumentTaskError("文档图中的审批版本已变化。")
            if decision is not None:
                service._validate_save_decision(user_id, approval_id, decision, token, identity.state_version)
                db.commit()
                graph.invoke(Command(resume=decision), context=context, config=identity.config, durability="sync")
    except (ApprovalCheckpointConflict, PostgresError, PoolTimeout) as exc:
        db.rollback()
        raise DocumentTaskError("文档审批恢复暂不可用，请刷新业务状态；不会自动重复保存。") from exc
    except DocumentTaskError:
        db.rollback()
        raise
    return context.result
