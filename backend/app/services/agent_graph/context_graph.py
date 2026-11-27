"""独立召回可并行，共享请求事务的工具和摘要阶段按序执行。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from langsmith import tracing_context

from app.services.chat_assembly_runtime import ChatAssemblyRuntime


class ContextGraphState(TypedDict, total=False):
    phase: str
    generation_id: str | None
    history_count: int
    memory_count: int
    tool_source_count: int
    knowledge_source_count: int
    final_source_labels: list[str]
    prompt_tokens: int


@dataclass(frozen=True)
class ContextGraphContext:
    service: Any
    assembly: ChatAssemblyRuntime


def _record(runtime: Runtime[ContextGraphContext], phase: str) -> dict:
    runtime.context.assembly.phases.append(phase)
    return {"phase": phase}


async def _snapshot(state: ContextGraphState, runtime: Runtime[ContextGraphContext]):
    context = runtime.context
    context.service.snapshot(context.assembly)
    return {**_record(runtime, "snapshot"), "generation_id": context.assembly.assistant_generation_id,
            "history_count": len(context.assembly.history_rows)}


async def _summary_preflight(state: ContextGraphState, runtime: Runtime[ContextGraphContext]):
    context = runtime.context
    await context.service.summary_preflight(context.assembly)
    return _record(runtime, "summary_preflight")


async def _retrieve(state: ContextGraphState, runtime: Runtime[ContextGraphContext]):
    context = runtime.context
    tasks = [asyncio.create_task(operation(context.assembly)) for operation in (
        context.service.retrieve_memory, context.service.retrieve_knowledge)]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    context.assembly.phases.extend(("memory", "rag"))
    return {"phase": "retrieval", "memory_count": context.assembly.memory_bundle.count,
            "knowledge_source_count": len(context.assembly.knowledge_context_result.sources)}


async def _tools(state: ContextGraphState, runtime: Runtime[ContextGraphContext]):
    context = runtime.context
    await context.service.execute_tools(context.assembly)
    return {**_record(runtime, "tools_subgraph"),
            "tool_source_count": len(context.assembly.external_context_result.sources)}


async def _summary(state: ContextGraphState, runtime: Runtime[ContextGraphContext]):
    context = runtime.context
    await context.service.refresh_summary(context.assembly)
    return _record(runtime, "summary")


async def _govern(state: ContextGraphState, runtime: Runtime[ContextGraphContext]):
    context = runtime.context
    await context.service.govern_prompt(context.assembly)
    return {**_record(runtime, "govern"),
            "prompt_tokens": context.assembly.governed_context.stats.total_tokens_estimate}


async def _finalize(state: ContextGraphState, runtime: Runtime[ContextGraphContext]):
    context = runtime.context
    context.assembly.result = context.service.finalize_context(context.assembly)
    _record(runtime, "context_ready")
    context.assembly.result.context_details["context_graph_phases"] = list(context.assembly.phases)
    context.assembly.result.context_stats["context_orchestrator"] = "langgraph"
    context.assembly.result.context_stats["context_graph_phases"] = list(context.assembly.phases)
    return {"phase": "context_ready",
            "final_source_labels": list(context.assembly.governed_context.final_source_map)}


@lru_cache(maxsize=1)
def build_context_graph():
    graph = StateGraph(ContextGraphState, context_schema=ContextGraphContext)
    nodes = [("snapshot", _snapshot), ("summary_preflight", _summary_preflight), ("retrieval", _retrieve), ("tools", _tools),
             ("summary", _summary), ("govern", _govern), ("context_ready", _finalize)]
    previous = START
    for name, function in nodes:
        graph.add_node(name, function)
        graph.add_edge(previous, name)
        previous = name
    graph.add_edge(previous, END)
    return graph.compile(name="aiws_chat_context")


async def run_context_graph(service: Any, assembly: ChatAssemblyRuntime):
    # 每个请求有独立assembly；不持久化Prompt正文/凭据，也不隐式重试业务步骤。
    with tracing_context(enabled=False):
        await build_context_graph().ainvoke(
            {"phase": "start"}, context=ContextGraphContext(service, assembly),
            config={"recursion_limit": 16},
        )
    if assembly.result is None:
        raise RuntimeError("上下文图未收口")
    return assembly.result
