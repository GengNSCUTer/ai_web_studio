"""候选记忆图：源快照→模型→结构/原文→冲突→短事务保存与游标→积压。"""

import asyncio
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import START, END, StateGraph
from langgraph.runtime import Runtime
from langsmith import tracing_context


class MemoryExtractionState(TypedDict, total=False):
    job_id: str
    lease_version: int
    phase: str
    ready: bool
    candidate_count: int
    control_request: bool


@dataclass
class MemoryExtractionContext:
    worker: Any
    job_id: str
    lease_version: int
    snapshot: Any = None
    raw: str | None = None
    suggestions: Any = None


async def _snapshot(state: MemoryExtractionState, runtime: Runtime[MemoryExtractionContext]):
    ctx = runtime.context
    ctx.snapshot = ctx.worker._load_snapshot(ctx.job_id, ctx.lease_version)
    return {"phase": "source_snapshot", "ready": ctx.snapshot is not None,
            "control_request": bool(ctx.snapshot and ctx.snapshot.get("control_request"))}


async def _control(state: MemoryExtractionState, runtime: Runtime[MemoryExtractionContext]):
    ctx = runtime.context
    completed = ctx.worker._complete_control(ctx.job_id, ctx.lease_version)
    return {"phase": "control_committed", "ready": completed}


async def _extract(state: MemoryExtractionState, runtime: Runtime[MemoryExtractionContext]):
    ctx = runtime.context
    ctx.raw = await asyncio.wait_for(ctx.worker.provider.complete_chat(**ctx.snapshot["provider_call"]), 45)
    return {"phase": "model_returned"}


async def _verify(state: MemoryExtractionState, runtime: Runtime[MemoryExtractionContext]):
    ctx = runtime.context
    decoded = ctx.worker._decode_candidates(ctx.raw, ctx.snapshot)
    ctx.raw = None
    ctx.suggestions = ctx.worker._verify_candidate_sources(ctx.job_id, ctx.lease_version, decoded)
    return {"phase": "evidence_checked", "ready": ctx.suggestions is not None,
            "candidate_count": len(ctx.suggestions or [])}


async def _conflicts(state: MemoryExtractionState, runtime: Runtime[MemoryExtractionContext]):
    ctx = runtime.context
    ctx.suggestions = await ctx.worker.conflicts.assess(user_id=ctx.snapshot["user_id"],
        project_id=ctx.snapshot["project_id"], suggestions=ctx.suggestions)
    return {"phase": "conflicts_checked"}


async def _persist(state: MemoryExtractionState, runtime: Runtime[MemoryExtractionContext]):
    ctx = runtime.context
    # 原文/撤销/冲突版本/租约锁内再检，候选和Job成功及游标同事务保存。
    completed = ctx.worker._complete(ctx.job_id, ctx.lease_version, ctx.suggestions)
    return {"phase": "business_committed" if completed else "lease_lost", "ready": completed}


async def _backlog(state: MemoryExtractionState, runtime: Runtime[MemoryExtractionContext]):
    runtime.context.worker._enqueue_backlog(runtime.context.job_id)
    return {"phase": "finished"}


def build_memory_extraction_graph():
    graph = StateGraph(MemoryExtractionState, context_schema=MemoryExtractionContext)
    for name, node in (("snapshot", _snapshot), ("extract", _extract), ("verify", _verify),
                       ("conflicts", _conflicts), ("persist", _persist), ("control", _control), ("backlog", _backlog)):
        graph.add_node(name, node)
    graph.add_edge(START, "snapshot")
    graph.add_conditional_edges("snapshot", lambda s: ("control" if s["control_request"] else "extract") if s["ready"] else END)
    graph.add_edge("extract", "verify")
    graph.add_conditional_edges("verify", lambda s: "conflicts" if s["ready"] else END)
    graph.add_edge("conflicts", "persist")
    graph.add_conditional_edges("persist", lambda s: "backlog" if s["ready"] else END)
    graph.add_conditional_edges("control", lambda s: "backlog" if s["ready"] else END)
    graph.add_edge("backlog", END)
    return graph.compile(name="aiws_memory_extraction_v1")


async def run_memory_extraction_graph(worker, job_id, lease_version):
    # Job是事实与恢复边界，不为短批次额外持久化原文/模型输出。中断由原Job租约重领。
    with tracing_context(enabled=False):
        return await build_memory_extraction_graph().ainvoke({"job_id": job_id, "lease_version": lease_version},
            context=MemoryExtractionContext(worker, job_id, lease_version), config={"recursion_limit": 16})
