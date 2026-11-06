"""文档Worker单Step图：显式阶段节点和提交转换，正文只留在本次Runtime或业务产物。"""

from dataclasses import dataclass
from functools import partial
import json
from typing import Any, TypedDict

from langgraph.graph import START, END, StateGraph
from langgraph.runtime import Runtime
from langsmith import tracing_context
from sqlalchemy import select

from app.models.agent_runtime import AgentArtifact, AgentStep
from app.services.durable_checkpoint_service import CheckpointIdentity, CheckpointLeaseLost, durable_checkpointer


@dataclass(frozen=True)
class DocumentCheckpointIdentity(CheckpointIdentity):
    @property
    def thread_id(self):
        return f"langgraph_document_v1:u:{self.user_id}:p:{self.project_id}:r:{self.run_id}:s:{self.step_id}"


class DocumentState(TypedDict, total=False):
    step_id: str
    stage: str
    phase: str
    ready: bool
    lease_version: int
    status: str
    artifact_id: str | None


@dataclass
class DocumentContext:
    worker: Any
    db: Any
    claim: Any
    run: Any
    prepared: Any = None
    result: Any = None
    saved_artifact: Any = None


async def _preflight(state: DocumentState, runtime: Runtime[DocumentContext]):
    from app.services.document_task_worker import prepare_document_claim
    from app.services.document_task_service import DocumentTaskError
    context = runtime.context
    context.prepared = prepare_document_claim(context.worker, context.db, context.claim)
    if context.prepared is None:
        raise CheckpointLeaseLost("文档步骤执行权已失效")
    saved = context.db.scalar(select(AgentArtifact).where(AgentArtifact.run_id == context.run.id,
        AgentArtifact.step_id == context.claim.step_id, AgentArtifact.user_id == context.run.user_id))
    if saved:
        step = context.db.get(AgentStep, context.claim.step_id)
        if json.loads(step.result_json or "{}").get("artifact_id") != saved.id:
            raise DocumentTaskError("文档产物与步骤记录不一致，需先对账。")
        context.saved_artifact, context.result = saved, json.loads(saved.content_json)
    context.db.commit()
    return {"stage": context.prepared[4], "ready": True, "phase": "preflight"}


async def _execute(state: DocumentState, runtime: Runtime[DocumentContext], *, stage):
    from app.services.document_task_worker import execute_document_stage
    from app.services.document_task_service import DocumentTaskError
    context = runtime.context
    if context.prepared is None:
        await _preflight(state, runtime)
    if context.prepared[4] != stage:
        raise DocumentTaskError("文档图恢复阶段与当前业务Step不一致。")
    if context.result is None:
        context.result = await execute_document_stage(context.worker, context.db, context.claim, context.prepared)
    if stage != "delivery":
        # 这些阶段只读取业务数据；正文已复制到本次Runtime，先结束只读事务再写图进度。
        context.db.commit()
    return {"phase": f"{stage}_ready", "stage": stage}


async def _commit(state: DocumentState, runtime: Runtime[DocumentContext], *, stage, transition):
    from app.services.document_task_worker import commit_document_result
    context = runtime.context
    # 原始模型返回不在图中。提交前退出后，未保存阶段重新生成，已提交Artifact则复用。
    if context.result is None:
        await _execute(state, runtime, stage=stage)
    if not commit_document_result(context.worker, context.db, context.claim, context.prepared, context.result,
            transition=transition, existing_artifact=context.saved_artifact):
        raise CheckpointLeaseLost("文档提交时租约已失效")
    return {"phase": f"{stage}_committed", "lease_version": context.claim.outbox_lease_version}


async def _reconcile(state: DocumentState, runtime: Runtime[DocumentContext]):
    context = runtime.context
    context.db.expire_all()
    step = context.db.get(AgentStep, context.claim.step_id)
    result = json.loads(step.result_json or "{}")
    outcome = {"phase": "reconciled", "status": step.status, "artifact_id": result.get("artifact_id"),
               "lease_version": context.claim.outbox_lease_version}
    context.db.commit()
    return outcome


async def _deliver_and_commit(state: DocumentState, runtime: Runtime[DocumentContext]):
    # deliver在持有事件/业务行锁的事务中准备文件和ProjectFile；该事务必须先提交，
    # 才能让独立连接的原生Saver获取同一事件锁，不能在两者之间插入图检查点。
    await _execute(state, runtime, stage="delivery")
    return await _commit(state, runtime, stage="delivery", transition="finish")


def build_document_graph(checkpointer=None):
    graph = StateGraph(DocumentState, context_schema=DocumentContext)
    graph.add_node("preflight", _preflight)
    for stage, transition in (("evidence", "draft"), ("draft", "export"), ("export", "download"), ("delivery", "finish")):
        if stage == "delivery":
            graph.add_node(stage, _deliver_and_commit)
            graph.add_edge(stage, "reconcile")
            continue
        graph.add_node(stage, partial(_execute, stage=stage))
        graph.add_node(f"save_{stage}", partial(_commit, stage=stage, transition=transition))
        graph.add_edge(stage, f"save_{stage}")
        graph.add_edge(f"save_{stage}", "reconcile")
    graph.add_node("reconcile", _reconcile)
    graph.add_edge(START, "preflight")
    graph.add_conditional_edges("preflight", lambda state: state["stage"],
                                {stage: stage for stage in ("evidence", "draft", "export", "delivery")})
    graph.add_edge("reconcile", END)
    return graph.compile(checkpointer=checkpointer, name="aiws_document_v1")


async def run_document_graph(worker, db, claim, run):
    context = DocumentContext(worker, db, claim, run)
    identity = DocumentCheckpointIdentity(run.user_id, run.project_id, run.id, claim.step_id,
                                         claim.outbox_event_id, worker.owner, claim.outbox_lease_version)
    url = db.get_bind().url
    db.commit()
    start = {"step_id": claim.step_id, "lease_version": claim.outbox_lease_version, "phase": "claimed"}
    with tracing_context(enabled=False):
        if url.get_backend_name() != "postgresql":
            await build_document_graph().ainvoke(start, context=context, config={"recursion_limit": 16})
            return
        try:
            async with durable_checkpointer(url, identity) as saver:
                graph = build_document_graph(saver)
                previous = await graph.aget_state(identity.config)
                # 新领取先按当前租约和业务产物预检，不能沿用旧尝试的调度尾部。
                resume = previous.next and previous.values.get("lease_version") == claim.outbox_lease_version
                await graph.ainvoke(None if resume else start, context=context,
                                    config=identity.config, durability="sync")
        except CheckpointLeaseLost:
            db.rollback()
