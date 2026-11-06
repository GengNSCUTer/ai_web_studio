"""索引快照构建图与Job完成图；Job/租约/ACK仍由基础设施维护。"""

from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import START, END, StateGraph
from langgraph.runtime import Runtime
from langsmith import tracing_context


class IndexState(TypedDict, total=False):
    generation_id: str
    phase: str
    chunk_count: int
    missing_count: int


def _chunks(state: IndexState, runtime: Runtime[Any]):
    ctx = runtime.context
    ctx.service._prepare_generation_chunks(ctx)
    return {"phase": "chunk_snapshot", "chunk_count": len(ctx.snapshot_chunks), "missing_count": len(ctx.missing)}


def _embed(state: IndexState, runtime: Runtime[Any]):
    runtime.context.service._embed_generation_chunks(runtime.context)
    return {"phase": "vectors_ready"}


def _persist(state: IndexState, runtime: Runtime[Any]):
    runtime.context.service._persist_generation_chunks(runtime.context)
    return {"phase": "inactive_chunks_committed"}


def _lexical(state: IndexState, runtime: Runtime[Any]):
    runtime.context.service._publish_generation_lexical(runtime.context)
    return {"phase": "bm25_manifest_ready"}


def build_index_snapshot_graph():
    graph = StateGraph(IndexState)
    for name, node in (("chunks", _chunks), ("embed", _embed), ("persist_chunks", _persist), ("lexical_manifest", _lexical)):
        graph.add_node(name, node)
    graph.add_edge(START, "chunks")
    graph.add_edge("chunks", "embed")
    graph.add_edge("embed", "persist_chunks")
    graph.add_edge("persist_chunks", "lexical_manifest")
    graph.add_edge("lexical_manifest", END)
    return graph.compile(name="aiws_knowledge_snapshot_v1")


def run_index_build_graph(context):
    with tracing_context(enabled=False):
        build_index_snapshot_graph().invoke({"generation_id": context.generation_id},
            context=context, config={"recursion_limit": 16})
    return context.result


class KnowledgeJobState(TypedDict, total=False):
    job_id: str
    job_type: str
    phase: str
    generation_id: str | None
    completed: bool


@dataclass
class KnowledgeJobContext:
    worker: Any
    lease: Any
    lost_lease: Any
    result: Any = None
    generation_id: str | None = None


def _parse(state: KnowledgeJobState, runtime: Runtime[KnowledgeJobContext]):
    ctx = runtime.context
    ctx.result = ctx.worker._execute_parse(ctx.lease)
    return {"phase": "parsed"}


def _complete_parse(state: KnowledgeJobState, runtime: Runtime[KnowledgeJobContext]):
    ctx = runtime.context
    completed = not ctx.lost_lease.is_set() and ctx.worker.coordinator.complete_parse(ctx.lease, ctx.result)
    return {"phase": "parse_committed", "completed": completed}


def _index(state: KnowledgeJobState, runtime: Runtime[KnowledgeJobContext]):
    ctx = runtime.context
    ctx.generation_id, ctx.result = ctx.worker._execute_index(ctx.lease)
    return {"phase": "inactive_generation_ready", "generation_id": ctx.generation_id}


def _activate(state: KnowledgeJobState, runtime: Runtime[KnowledgeJobContext]):
    ctx = runtime.context
    completed = not ctx.lost_lease.is_set() and ctx.worker.coordinator.activate_generation(ctx.lease, ctx.generation_id, ctx.result)
    return {"phase": "activation_committed", "completed": completed}


def _route(state):
    if state["job_type"] == "parse_document":
        return "parse"
    if state["job_type"] == "index_document":
        return "index"
    raise ValueError("不支持的知识库Job类型")


def build_knowledge_job_graph():
    graph = StateGraph(KnowledgeJobState, context_schema=KnowledgeJobContext)
    for name, node in (("parse", _parse), ("complete_parse", _complete_parse), ("index", _index), ("activate", _activate)):
        graph.add_node(name, node)
    graph.add_conditional_edges(START, _route)
    graph.add_edge("parse", "complete_parse")
    graph.add_edge("complete_parse", END)
    graph.add_edge("index", "activate")
    graph.add_edge("activate", END)
    return graph.compile(name="aiws_knowledge_job_v1")


def run_knowledge_job_graph(context):
    with tracing_context(enabled=False):
        return build_knowledge_job_graph().invoke({"job_id": context.lease.job_id, "job_type": context.lease.job_type,
            "generation_id": None, "completed": False}, context=context, config={"recursion_limit": 16})
