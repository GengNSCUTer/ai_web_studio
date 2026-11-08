"""一个Durable Step的预检、执行提交和对账图；原Worker仍负责领取/心跳/重试。"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langsmith import tracing_context
from app.models.agent_runtime import AgentStep
from app.services.durable_checkpoint_service import (
    CheckpointIdentity, CheckpointLeaseLost, durable_checkpointer,
)


class DurableGraphState(TypedDict, total=False):
    phase: str
    ready: bool
    step_id: str
    status: str
    artifact_id: str | None
    lease_version: int


@dataclass
class DurableGraphContext:
    worker: Any
    db: Any
    claim: Any
    run: Any
    prepared: Any = None


def _outcome(context):
    context.db.expire_all()
    step = context.db.get(AgentStep, context.claim.step_id)
    if step is None:
        raise CheckpointLeaseLost("步骤已不存在")
    result = json.loads(step.result_json or "{}")
    outcome = {"step_id": step.id, "status": step.status,
               "artifact_id": result.get("artifact_id"), "lease_version": step.lease_version}
    context.db.commit()
    return outcome


async def _preflight(state: DurableGraphState, runtime: Runtime[DurableGraphContext]):
    context = runtime.context
    # 统一业务预检先核对当前版本/授权，再由共享恢复决策复用已提交结果。
    # 原生图位置不授予复用权限，也不证明外部请求成功。
    context.prepared = context.worker._prepare_tool_execution(context.db, context.claim)
    return {"phase": "preflight", "ready": context.prepared is not None}


async def _execute_commit(state: DurableGraphState, runtime: Runtime[DurableGraphContext]):
    context = runtime.context
    # 恢复时只有图State，不能恢复Session/客户端；重新预检并重建请求对象。
    # preflight会先查看已提交Artifact，执行成功但图记录滞后时直接复用。
    if context.prepared is None:
        reused = await _preflight(state, runtime)
        if not reused["ready"]:
            return reused
    executor, call, run, step, event = context.prepared
    result, events = await context.worker._execute_with_heartbeat(executor, call, context.claim)
    # 原结果质量/幂等/租约校验和短事务作为一个业务动作；不把原始返回写入图。
    context.worker._complete_tool_execution(context.db, context.claim, run, step, event, call, result, events)
    return {"phase": "business_committed", **_outcome(context)}


async def _reconcile(state: DurableGraphState, runtime: Runtime[DurableGraphContext]):
    return {"phase": "reconciled", **_outcome(runtime.context)}


def build_durable_graph(checkpointer=None):
    graph = StateGraph(DurableGraphState, context_schema=DurableGraphContext)
    graph.add_node("preflight", _preflight)
    graph.add_node("execute_commit", _execute_commit)
    graph.add_node("reconcile", _reconcile)
    graph.add_edge(START, "preflight")
    graph.add_conditional_edges("preflight", lambda state: "execute_commit" if state["ready"] else "reconcile",
                                {"execute_commit": "execute_commit", "reconcile": "reconcile"})
    graph.add_edge("execute_commit", "reconcile")
    graph.add_edge("reconcile", END)
    return graph.compile(checkpointer=checkpointer, name="aiws_durable_tool_v1")


async def run_durable_graph(worker, db, claim, run):
    context = DurableGraphContext(worker, db, claim, run)
    identity = CheckpointIdentity(run.user_id, run.project_id, run.id, claim.step_id,
                                  claim.outbox_event_id, worker.owner, claim.outbox_lease_version)
    url = db.get_bind().url
    # 图I/O不持有业务只读事务。实际调用前仍由原服务重新检查业务状态。
    db.commit()
    with tracing_context(enabled=False):
        if url.get_backend_name() != "postgresql":
            # SQLite仅用于原单测；执行同一节点图，不假装具有PG检查点持久化。
            await build_durable_graph().ainvoke({"step_id": claim.step_id}, context=context,
                                                config={"recursion_limit": 16})
            return
        try:
            async with durable_checkpointer(url, identity) as saver:
                graph = build_durable_graph(saver)
                previous = await graph.aget_state(identity.config)
                # 新租约代表新尝试，必须重新预检。旧图可能停在已提交重排后的
                # reconcile，直接续它会只对账、不执行，白占新租约到期。
                # 已提交产物仍由preflight复用，不因重新进图重复外部副作用。
                resume = previous.next and previous.values.get("lease_version") == claim.outbox_lease_version
                graph_input = None if resume else {"step_id": claim.step_id,
                    "lease_version": claim.outbox_lease_version, "ready": False}
                await graph.ainvoke(graph_input, context=context, config=identity.config, durability="sync")
        except CheckpointLeaseLost:
            db.rollback()
            # 旧执行者退出，不换引擎、不把新Worker的Step标成失败。
            return
